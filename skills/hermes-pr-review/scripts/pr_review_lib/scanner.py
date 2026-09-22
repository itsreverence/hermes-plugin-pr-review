"""Deterministic shadow discovery and routing; no models or review completion.

Contract:
  load_repos(path) / validate_repos(list) -> exact, nonempty owner/repo list.
  ScanGitHub(**GitHub_budget_options, max_pages=20).enumerate(repos) -> metadata.
  Scanner(root, clock=time.time, lease_seconds=1800, max_attempts=3)
    .scan(repos, github, *, workflow_key=None) -> status dict; only a complete
       enumeration is applied. None uses the trusted installed workflow_digest;
       an explicit bounded revision key is for trusted worker/test callers.
    .status() -> {wakeAgent, candidates, versions, atomic: False, ...}.
    .claim() -> candidate with lease_token/lease_expires, or None (global busy
                or no due work). Each claim consumes one persisted attempt.
    .finish(id, lease_token, result_status, *, attempt_id=None, retry_after=60)
       -> version. completed/skipped require an attempt ID and route to done;
          failed/incomplete/stale route to backoff, or held when exhausted;
          held routes to held. Retry delay is 0..86400 seconds.

These are TRUSTED WORKER APIs, never model-result commands. The worker must
bind and verify the actual shared State attempt and snapshot BEFORE finish.
An attempt_id stored here is a routing reference, NOT proof of completion.
Only shared prepare/finalize coordinates manual work and proves completion.
There is intentionally no public acknowledgment CLI or model-facing schema.

Versions preserve immutable metadata and identity, even when superseded or
excluded. Their mutable routing status is pending/backoff/leased/held/done.
Expired claims retry (up to their persisted limit); old tokens cannot finish.
One database-wide worker lease survives scans and candidate retirement. It
fences queue writes, not external model execution: workers must still use
shared State. A successful scan excludes drafts, vanished PRs, and unenrolled
repos. GitHub pagination is NOT an atomic snapshot: concurrent mutations can
still move items between pages. Workers must recheck through shared prepare.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import re
import sqlite3
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from .artifacts import digest, managed_root, private_file, read_json, reject_symlinks
from .github import GitHub, GitHubError, parse_ref


class ScannerError(RuntimeError):
    """Safe routing/lease/database error; never a remote response body."""


def validate_repos(value):
    if not isinstance(value, list) or not value:
        raise ValueError("repos must be an explicit nonempty JSON list")
    seen = set()
    for repo in value:
        if not isinstance(repo, str) or parse_ref(repo + "#1") != repo + "#1":
            raise ValueError("repository must be an exact owner/repo")
        if repo.casefold() in seen:
            raise ValueError("duplicate repository in allowlist")
        seen.add(repo.casefold())
    return list(value)


def load_repos(path):
    return validate_repos(read_json(path))


def _metadata(repo, item):
    try:
        number = item["number"]
        if type(number) is not int or not 1 <= number <= 9999999999999999999:
            raise ValueError
        ref = parse_ref(f"{repo}#{number}")
        if (parse_ref(item["html_url"]).casefold() != ref.casefold() or
                not item["html_url"].startswith("https://github.com/") or
                not isinstance(item["base"]["repo"]["full_name"], str) or
                item["base"]["repo"]["full_name"].casefold() != repo.casefold()):
            raise ValueError
        if item["state"] != "open" or type(item["draft"]) is not bool:
            raise ValueError
        for side in ("head", "base"):
            sha = item[side]["sha"]
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                raise ValueError
        if not isinstance(item["title"], str) or len(item["title"]) > 1024:
            raise ValueError
        if item["body"] is not None and (not isinstance(item["body"], str) or len(item["body"]) > 100_000):
            raise ValueError
        return {"ref": ref, "repo": repo, "number": number, "url": item["html_url"],
                "head_sha": item["head"]["sha"], "base_sha": item["base"]["sha"],
                "title": item["title"], "body": item["body"] or "",
                "state": "open", "draft": item["draft"]}
    except (KeyError, TypeError, ValueError):
        raise GitHubError("GitHub returned invalid pull listing metadata") from None


class ScanGitHub(GitHub):
    """One inherited request/deadline budget across every allowlisted repo.

    Fetch an empty sentinel page even after a short page: do not mistake a
    partial nonempty response for an exhausted listing. Duplicate identities,
    corrupt items and any exhausted budget fail the ENTIRE scan. max_pages is
    per repository, including the sentinel; max_requests includes retries.
    No metadata-per-PR requests, anonymous transport, search, or repo discovery.
    """

    def __init__(self, *, max_pages=20, **kwargs):
        super().__init__(**kwargs)
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        self.max_pages = max_pages

    def _request(self, endpoint, *, optional=False):
        # The shared client owns auth/retry/deadline/JSON parsing. Capture only
        # its final response headers, which it otherwise does not expose.
        original = self.transport
        header = ""

        def capture(argv, **kwargs):
            nonlocal header
            proc = (original or subprocess.run)(argv, **kwargs)
            if isinstance(proc.stdout, str):
                header = proc.stdout.replace("\r\n", "\n").partition("\n\n")[0]
            return proc

        self.transport = capture
        try:
            result = super()._request(endpoint, optional=optional)
        finally:
            self.transport = original
        lines = header.split("\n")
        if not re.fullmatch(r"HTTP/\S+ 200(?: .*)?", lines[0]):
            raise GitHubError("GitHub pull listing was not a complete HTTP 200 response")
        links = [line.partition(":")[2].strip() for line in lines[1:]
                 if line.partition(":")[0].lower().strip() == "link"]
        self._page_links = {}
        if len(links) > 1:
            raise GitHubError("GitHub returned ambiguous pagination headers")
        for part in links[0].split(",") if links else []:
            match = re.fullmatch(r'\s*<([^<>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
            if not match:
                raise GitHubError("GitHub returned malformed pagination headers")
            try:
                url = urlsplit(match[1])
                pages = parse_qs(url.query).get("page", [])
                if (url.scheme != "https" or url.netloc != "api.github.com" or
                        url.path.casefold() != ("/" + endpoint.split("?")[0]).casefold() or
                        url.fragment or len(pages) != 1 or
                        not re.fullmatch(r"[1-9][0-9]{0,8}", pages[0]) or match[2] in self._page_links):
                    raise ValueError
                self._page_links[match[2]] = int(pages[0])
            except ValueError:
                raise GitHubError("GitHub returned invalid pagination target") from None
        return result

    def enumerate(self, repos):
        repos = validate_repos(repos)
        self._begin()
        candidates = []
        for repo in repos:
            seen = set()
            required_through = 0
            for page in range(1, self.max_pages + 1):
                items = self._request(f"repos/{repo}/pulls?state=open&sort=created&direction=asc&per_page=100&page={page}")
                if not isinstance(items, list) or len(items) > 100:
                    raise GitHubError("GitHub returned invalid pull listing")
                next_page = self._page_links.get("next")
                if next_page is not None and next_page != page + 1:
                    raise GitHubError("GitHub returned inconsistent next page")
                required_through = max(required_through, next_page or 0, self._page_links.get("last", 0))
                if not items:
                    if required_through >= page:
                        raise GitHubError("GitHub pull listing ended before advertised pages")
                    break
                for item in items:
                    metadata = _metadata(repo, item)
                    if metadata["number"] in seen:
                        raise GitHubError("GitHub pull pagination repeated an identity")
                    seen.add(metadata["number"])
                    if not metadata["draft"]:
                        candidates.append(metadata)
            else:
                raise GitHubError("GitHub pull pagination page budget exhausted")
        self._remaining()
        return candidates


def _bounded(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} out of bounds")
    return value


class Scanner:
    def __init__(self, root, *, clock=time.time, lease_seconds=1800, max_attempts=3):
        self.root = managed_root(root)
        self.path = self.root / "scanner.sqlite3"
        self.clock = clock
        self.lease_seconds = _bounded(lease_seconds, 1, 3600, "lease_seconds")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be an integer between 1 and 100")
        self.max_attempts = max_attempts
        private_file(self.path)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ScannerError("unsupported scanner schema")
            db.execute("""CREATE TABLE IF NOT EXISTS control (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                next_scan INTEGER NOT NULL DEFAULT 0, applied_scan INTEGER NOT NULL DEFAULT 0,
                scanned_at REAL, repos TEXT, lease_id TEXT, lease_token TEXT, lease_expires REAL
            )""")
            db.execute("INSERT OR IGNORE INTO control(singleton) VALUES (1)")
            db.execute("""CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY, ref TEXT NOT NULL COLLATE NOCASE,
                identity TEXT NOT NULL, metadata TEXT NOT NULL,
                created REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                retired_reason TEXT, status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','backoff','leased','held','done')),
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                due REAL NOT NULL, result_status TEXT, attempt_id TEXT
            )""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS current_ref ON candidates(ref) WHERE active=1")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connection(self):
        private_file(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = reject_symlinks(str(self.path) + suffix)
            if sidecar.exists():
                private_file(sidecar)
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error as exc:
            if db:
                db.rollback()
            raise ScannerError("scanner database unavailable or corrupt; preserve for inspection") from exc
        except BaseException:
            if db:
                db.rollback()
            raise
        finally:
            if db:
                db.close()

    def _now(self):
        return _bounded(self.clock(), 0, 1e15, "clock")

    @staticmethod
    def _version(row):
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["active"] = bool(result["active"])
        return result

    @staticmethod
    def _release(db):
        db.execute("UPDATE control SET lease_id=NULL,lease_token=NULL,lease_expires=NULL WHERE singleton=1")

    def _expire(self, db, now):
        lease = db.execute("SELECT * FROM control WHERE singleton=1").fetchone()
        if lease["lease_id"] and lease["lease_expires"] <= now:
            db.execute("""UPDATE candidates SET status=CASE WHEN attempts >= max_attempts
                THEN 'held' ELSE 'backoff' END, due=?, result_status='lease_expired'
                WHERE id=? AND status='leased'""", (now, lease["lease_id"]))
            self._release(db)

    def _status(self, db, now):
        self._expire(db, now)
        versions = [self._version(row) for row in db.execute("SELECT * FROM candidates ORDER BY created,ref,id")]
        candidates = [row for row in versions if row["active"] and row["status"] in ("pending", "backoff") and row["due"] <= now]
        control = db.execute("SELECT * FROM control WHERE singleton=1").fetchone()
        return {"wakeAgent": bool(candidates) and control["lease_id"] is None,
                "candidates": candidates, "versions": versions,
                "workerBusy": control["lease_id"] is not None,
                "applied_scan": control["applied_scan"], "scanned_at": control["scanned_at"],
                "repos": json.loads(control["repos"]) if control["repos"] else [],
                "atomic": False}

    def status(self):
        with self.connection() as db:
            return self._status(db, self._now())

    def scan(self, repos, github, *, workflow_key=None):
        repos = validate_repos(repos)
        if workflow_key is None:
            from .workflow import workflow_digest
            workflow_key = workflow_digest()
        if not isinstance(workflow_key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", workflow_key):
            raise ValueError("workflow_key must be an exact bounded trusted revision key")
        # Reserve order without holding a write lock during network requests.
        with self.connection() as db:
            db.execute("UPDATE control SET next_scan=next_scan+1 WHERE singleton=1")
            sequence = db.execute("SELECT next_scan FROM control WHERE singleton=1").fetchone()[0]
        metadata = github.enumerate(repos)
        with self.connection() as db:
            now = self._now()
            applied = db.execute("SELECT applied_scan FROM control WHERE singleton=1").fetchone()[0]
            if sequence <= applied:
                raise ScannerError("scan superseded by a newer successful scan")
            current = {row["ref"].casefold(): dict(row) for row in db.execute("SELECT * FROM candidates WHERE active=1")}
            observed = set()
            for item in metadata:
                item = {**item, "workflow_key": workflow_key}
                key = item["ref"].casefold()
                observed.add(key)
                identity = digest({"version": 1, "ref": key, "workflow_key": workflow_key, **{
                    name: item[name] for name in ("head_sha", "base_sha", "title", "body")}})
                old = current.get(key)
                if old and old["identity"] == identity:
                    continue  # Preserve pending/retry/held/done and any active lease.
                if old:
                    db.execute("UPDATE candidates SET active=0,retired_reason='superseded' WHERE id=?", (old["id"],))
                db.execute("""INSERT INTO candidates(id,ref,identity,metadata,created,max_attempts,due)
                    VALUES (?,?,?,?,?,?,?)""", (uuid.uuid4().hex, item["ref"], identity,
                                              json.dumps(item, sort_keys=True), now, self.max_attempts, now))
            for key, row in current.items():
                if key not in observed:
                    db.execute("UPDATE candidates SET active=0,retired_reason='excluded' WHERE id=?", (row["id"],))
            db.execute("UPDATE control SET applied_scan=?,scanned_at=?,repos=? WHERE singleton=1",
                       (sequence, now, json.dumps(repos)))
            return self._status(db, now)

    def claim(self):
        with self.connection() as db:
            now = self._now()
            self._expire(db, now)
            if db.execute("SELECT lease_id FROM control WHERE singleton=1").fetchone()[0]:
                return None
            row = db.execute("""SELECT * FROM candidates WHERE active=1
                AND status IN ('pending','backoff') AND due<=? AND attempts<max_attempts
                ORDER BY due,created,ref,id LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            token, expires = uuid.uuid4().hex, now + self.lease_seconds
            db.execute("UPDATE candidates SET status='leased',attempts=attempts+1 WHERE id=?", (row["id"],))
            db.execute("UPDATE control SET lease_id=?,lease_token=?,lease_expires=? WHERE singleton=1",
                       (row["id"], token, expires))
            result = self._version(db.execute("SELECT * FROM candidates WHERE id=?", (row["id"],)).fetchone())
            result.update(lease_token=token, lease_expires=expires)
            return result

    def finish(self, candidate_id, lease_token, result_status, *, attempt_id=None, retry_after=60):
        if result_status not in ("completed", "skipped", "failed", "incomplete", "stale", "held"):
            raise ValueError("invalid worker result status")
        if attempt_id is not None and (not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id)):
            raise ValueError("invalid shared attempt ID")
        if result_status in ("completed", "skipped") and attempt_id is None:
            raise ValueError("done routing requires a worker-verified shared attempt ID")
        retry_after = _bounded(retry_after, 0, 86400, "retry_after")
        with self.connection() as db:
            now = self._now()
            lease = db.execute("SELECT * FROM control WHERE singleton=1").fetchone()
            row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if (not row or not row["active"] or row["status"] != "leased" or
                    not lease_token or lease["lease_id"] != candidate_id or
                    lease["lease_token"] != lease_token or lease["lease_expires"] <= now):
                raise ScannerError("candidate lease is inactive, superseded, or expired")
            if result_status in ("completed", "skipped"):
                status = "done"
            elif result_status == "held" or row["attempts"] >= row["max_attempts"]:
                status = "held"
            else:
                status = "backoff"
            db.execute("UPDATE candidates SET status=?,due=?,result_status=?,attempt_id=? WHERE id=?",
                       (status, now + retry_after, result_status, attempt_id, candidate_id))
            self._release(db)
            return self._version(db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone())
