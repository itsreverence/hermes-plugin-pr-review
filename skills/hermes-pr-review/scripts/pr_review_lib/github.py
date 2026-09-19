"""Read-only, bounded GitHub context collection using stdlib and ``gh`` only.

Public interface: parse_ref(text), GitHub.metadata(ref), GitHub.collect(ref).
``transport`` has subprocess.run's signature and returns a CompletedProcess;
``sleep`` and ``clock`` are injectable. No local checkout, model, or state I/O.

Collection is deliberately conservative: API limits, absent/binary/inconsistent
patches, unsafe/unknown config, incomplete docs discovery, and racing metadata
produce incomplete_reasons. Authentication, permission, transport, and request
budget failures raise sanitized GitHubError instead of masquerading as absence.
A caller MUST NOT treat an incomplete snapshot as a clean review. Patches are
kept verbatim or omitted whole, never cut through a hunk. Explicitly ignored
files remain in files with ignored=True and patch=None; coverage gaps in any
non-ignored file are incomplete. No implicit generated-file exclusions apply.
"""
from __future__ import annotations

import base64
import binascii
import fnmatch
from email.utils import parsedate_to_datetime
import json
import math
import re
import subprocess
import time
from urllib.parse import quote


_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_REPO = r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}"
_TARGET = rf"(?P<owner>{_OWNER})/(?P<repo>{_REPO})"
_SHORT = re.compile(rf"{_TARGET}#(?P<number>[1-9][0-9]{{0,18}})")
_URL = re.compile(rf"https://github\.com/{_TARGET}/pull/(?P<number>[1-9][0-9]{{0,18}})")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_HUNK = re.compile(r"@@ -([0-9]{1,10})(?:,([0-9]{1,10}))? \+([0-9]{1,10})(?:,([0-9]{1,10}))? @@(?: .*)?")
_MISSING = object()
CONFIG_PATHS = (".github/hermes-pr-reviewer.json", ".hermes/pr-reviewer.json")
DEFAULT_DOC_PATHS = (
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "CLAUDE.md", ".cursorrules",
    ".github/copilot-instructions.md", "ARCHITECTURE.md", "WORKFLOW.md",
    "docs/ARCHITECTURE.md", "docs/WORKFLOW.md",
)
MAX_RESPONSE_CHARS = 16_000_000
MAX_DOC_PATHS = 32
MAX_COMPARE_FILES = 300


class GitHubError(Exception):
    """A safe operational error, containing no remote body or stderr."""


def parse_ref(text: str) -> str:
    """Accept only an exact github.com PR URL or owner/repo#positive_number.

    Do not trim, decode, repair suffixes, or accept leading-zero PR numbers.
    GitHub's case-insensitive names retain their supplied spelling.
    """
    match = (_SHORT.fullmatch(text) or _URL.fullmatch(text)) if isinstance(text, str) else None
    if not match or match["repo"] in {".", ".."}:
        raise ValueError("PR reference must be an exact GitHub PR URL or owner/repo#positive_number")
    return f'{match["owner"]}/{match["repo"]}#{match["number"]}'


def _safe_path(value: object, *, pattern: bool = False) -> bool:
    if not isinstance(value, str) or not value or len(value) > 512 or value != value.strip():
        return False
    if value.startswith(("/", "~", "-")) or "\\" in value or "%" in value:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return False
    return pattern or not any(char in value for char in "*[]")


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(text):
    def reject_constant(_):
        raise ValueError("non-finite JSON number")
    return json.loads(text, object_pairs_hook=_object_pairs, parse_constant=reject_constant)


def _integer(value):
    return type(value) is int and value >= 0


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _complete_patch(patch, additions, deletions):
    """Check hunk and file counts, catching missing/truncated API patch text."""
    if not isinstance(patch, str) or not patch or "\x00" in patch:
        return False
    old = new = plus = minus = 0
    in_hunk = False
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    for line in lines:
        match = _HUNK.fullmatch(line)
        if match:
            if in_hunk and (old or new):
                return False
            old = int(match[2]) if match[2] is not None else 1
            new = int(match[4]) if match[4] is not None else 1
            in_hunk = True
        elif not in_hunk:
            return False
        elif line == "\\ No newline at end of file":
            continue
        elif line.startswith("+"):
            new -= 1
            plus += 1
        elif line.startswith("-"):
            old -= 1
            minus += 1
        elif line.startswith(" "):
            old -= 1
            new -= 1
        else:
            return False
        if old < 0 or new < 0:
            return False
    return in_hunk and old == new == 0 and plus == additions and minus == deletions


class GitHub:
    """A reusable, single-operation-budgeted, read-only API client.

    Each metadata()/collect() call starts a fresh global budget; retries and the
    final race check share that budget. This instance is not thread-safe.
    All commands have fixed argv, explicit GET/hostname, no shell or pagination.
    Default limits: 20s/request, 3 attempts, 64 requests, 120s/operation,
    120k patch characters and 60k doc characters. Oversized patches/docs are
    omitted, not silently clipped. Config allows only extraDocPaths and
    ignorePatterns (no graph, command, plugin, or executable options).
    """

    def __init__(self, *, transport=None, sleep=None, clock=None,
                 request_timeout=20, max_attempts=3, max_requests=64,
                 total_timeout=120, max_patch_chars=120_000, max_doc_chars=60_000):
        for value in (request_timeout, total_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite positive numbers")
        for value in (max_attempts, max_requests, max_patch_chars, max_doc_chars):
            if type(value) is not int or value < 1:
                raise ValueError("Budgets must be positive integers")
        self.transport = transport
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.request_timeout = request_timeout
        self.max_attempts = max_attempts
        self.max_requests = max_requests
        self.total_timeout = total_timeout
        self.max_patch_chars = max_patch_chars
        self.max_doc_chars = max_doc_chars

    def _begin(self):
        self._deadline = self.clock() + self.total_timeout
        self._requests = 0

    def _remaining(self):
        remaining = self._deadline - self.clock()
        if remaining <= 0:
            raise GitHubError("GitHub time budget exhausted")
        return remaining

    def _request(self, endpoint, *, optional=False):
        # Only internally assembled REST repo endpoints are ever passed here.
        for attempt in range(self.max_attempts):
            remaining = self._remaining()
            if self._requests >= self.max_requests:
                raise GitHubError("GitHub request budget exhausted")
            self._requests += 1
            argv = ["gh", "api", "--hostname", "github.com", "--method", "GET",
                    "--include", "--header", "Accept: application/vnd.github+json",
                    "--header", "X-GitHub-Api-Version: 2022-11-28", endpoint]
            try:
                proc = (self.transport or subprocess.run)(
                    argv, text=True, capture_output=True, timeout=min(self.request_timeout, remaining),
                    check=False, stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                raise GitHubError("GitHub CLI unavailable or request timed out") from None
            self._remaining()
            stdout = proc.stdout
            if not isinstance(stdout, str) or len(stdout) > MAX_RESPONSE_CHARS:
                raise GitHubError("GitHub response exceeds size limit or is malformed")
            head, sep, body = stdout.replace("\r\n", "\n").partition("\n\n")
            lines = head.split("\n")
            status_match = re.fullmatch(r"HTTP/\S+ ([0-9]{3})(?: .*)?", lines[0])
            if not sep or not status_match:
                # gh auth failures often have no HTTP response. Never echo stderr.
                raise GitHubError("GitHub CLI request failed or returned malformed HTTP")
            status = int(status_match[1])
            headers = {}
            for line in lines[1:]:
                key, colon, value = line.partition(":")
                if colon:
                    headers[key.lower().strip()] = value.strip()
            if status == 404 and optional:
                return _MISSING
            if 200 <= status < 300 and proc.returncode == 0:
                try:
                    return _json(body)
                except (ValueError, RecursionError):
                    raise GitHubError("GitHub returned invalid JSON") from None
            rate_limited = status == 429 or (status == 403 and (
                "retry-after" in headers or headers.get("x-ratelimit-remaining") == "0"))
            retryable = rate_limited or status in {500, 502, 503, 504}
            if not retryable or attempt + 1 == self.max_attempts:
                raise GitHubError(f"GitHub GET failed (HTTP {status})")
            delay = min(2 ** attempt, 30)
            retry_after = headers.get("retry-after")
            if retry_after is not None:
                if re.fullmatch(r"[0-9]{1,9}", retry_after):
                    delay = max(delay, int(retry_after))
                else:
                    try:
                        retry_time = parsedate_to_datetime(retry_after).timestamp()
                        delay = max(delay, retry_time - time.time())
                    except (TypeError, ValueError, OverflowError):
                        raise GitHubError("GitHub returned invalid retry timing") from None
            elif rate_limited and headers.get("x-ratelimit-reset"):
                reset = headers["x-ratelimit-reset"]
                if not re.fullmatch(r"[0-9]{1,12}", reset):
                    raise GitHubError("GitHub returned invalid retry timing")
                delay = max(delay, int(reset) - time.time())
            if delay > 30 or delay >= self._remaining():
                raise GitHubError("GitHub retry exceeds time budget")
            self.sleep(delay)
        raise GitHubError("GitHub attempt budget exhausted")

    def metadata(self, ref: str) -> dict:
        """Fetch validated PR identity, SHAs, state and display metadata."""
        canonical = parse_ref(ref)
        self._begin()
        return self._metadata(canonical)

    def _metadata(self, canonical):
        repo, number_text = canonical.split("#")
        number = int(number_text)
        data = self._request(f"repos/{repo}/pulls/{number}")
        try:
            if not isinstance(data, dict):
                raise ValueError
            actual_repo = data["base"]["repo"]["full_name"]
            url_ref = parse_ref(data["html_url"])
            identity = parse_ref(f"{actual_repo}#{number}")
            if (identity.casefold() != canonical.casefold() or
                    url_ref.casefold() != canonical.casefold() or
                    type(data["number"]) is not int or data["number"] != number or
                    not data["html_url"].startswith("https://github.com/")):
                raise ValueError
            if not _sha(data["head"]["sha"]) or not _sha(data["base"]["sha"]):
                raise ValueError
            if (data["state"] not in ("open", "closed") or type(data["draft"]) is not bool or
                    not _integer(data["changed_files"]) or not isinstance(data["title"], str) or
                    len(data["title"]) > 1024 or
                    (data["body"] is not None and not isinstance(data["body"], str))):
                raise ValueError
            if data["body"] is not None and len(data["body"]) > 100_000:
                raise ValueError
            return {"repo": actual_repo, "number": number, "url": data["html_url"],
                    "head_sha": data["head"]["sha"], "base_sha": data["base"]["sha"],
                    "state": data["state"], "draft": data["draft"], "title": data["title"],
                    "body": data["body"] or "", "changed_files": data["changed_files"]}
        except (KeyError, TypeError, ValueError):
            raise GitHubError("GitHub returned invalid PR metadata or target identity") from None

    def collect(self, ref: str, *, stage: str = "review") -> dict:
        """Return a SHA-pinned snapshot; incomplete_reasons is authoritative."""
        if stage not in {"triage", "review"}:
            raise ValueError("invalid collection stage")
        canonical = parse_ref(ref)
        self._begin()
        result = self._metadata(canonical)
        result.update(merge_base_sha=None, files=[], docs={}, policy={"config_path": None, "extraDocPaths": [],
                                              "ignorePatterns": []}, incomplete_reasons=[])
        reasons = result["incomplete_reasons"]
        repo, base, head = result["repo"], result["base_sha"], result["head_sha"]
        compare = self._request(f"repos/{repo}/compare/{base}...{head}?per_page=100&page=1")
        for config_path in CONFIG_PATHS:
            text = self._document(repo, base, config_path, reasons, limit=16_000, config=True)
            if text is None:
                continue
            result["policy"]["config_path"] = config_path
            try:
                config = _json(text)
                if not isinstance(config, dict) or set(config) - {"extraDocPaths", "ignorePatterns"}:
                    raise ValueError
                for key, max_items in (("extraDocPaths", 24), ("ignorePatterns", 64)):
                    values = config.get(key, [])
                    if (not isinstance(values, list) or len(values) > max_items or
                            any(not _safe_path(value, pattern=(key == "ignorePatterns")) for value in values)):
                        raise ValueError
                result["policy"].update(config)
            except (ValueError, RecursionError):
                reasons.append("invalid_config")
            break  # Ordered config precedence, as in the legacy collector.
        self._files(compare, result, stage=stage)
        tree = self._request(f"repos/{repo}/git/trees/{base}?recursive=1")
        discovered = self._doc_paths(tree, reasons)
        paths = list(dict.fromkeys([*DEFAULT_DOC_PATHS, *result["policy"]["extraDocPaths"], *discovered]))
        if len(paths) > MAX_DOC_PATHS:
            reasons.append("docs_count_limit")
        remaining = self.max_doc_chars
        for path in paths[:MAX_DOC_PATHS]:
            if remaining <= 0:
                reasons.append("docs_budget")
                break
            text = self._document(repo, base, path, reasons, limit=remaining)
            if text is not None:
                result["docs"][path] = text
                remaining -= len(text)
        final = self._metadata(canonical)
        keys = ("base_sha", "head_sha", "state", "draft", "changed_files")
        if stage == "triage":
            keys += ("title", "body")
        if any(final[key] != result[key] for key in keys):
            reasons.append("stale_snapshot")
        result["incomplete_reasons"] = list(dict.fromkeys(reasons))
        return result

    def _files(self, compare, result, *, stage="review"):
        reasons = result["incomplete_reasons"]
        if not isinstance(compare, dict) or not isinstance(compare.get("files"), list):
            reasons.append("invalid_compare")
            return
        base_commit = compare.get("base_commit")
        if not isinstance(base_commit, dict) or base_commit.get("sha") != result["base_sha"]:
            reasons.append("compare_identity_mismatch")
        merge_base = compare.get("merge_base_commit")
        if not isinstance(merge_base, dict) or not _sha(merge_base.get("sha")):
            reasons.append("invalid_merge_base")
        else:
            result["merge_base_sha"] = merge_base["sha"]
        files = compare["files"]
        if len(files) >= MAX_COMPARE_FILES or result["changed_files"] >= MAX_COMPARE_FILES:
            reasons.append("compare_file_limit")
        if len(files) != result["changed_files"]:
            reasons.append("file_count_mismatch")
        remaining = self.max_patch_chars
        seen = set()
        for item in files[:MAX_COMPARE_FILES]:
            if (not isinstance(item, dict) or not _safe_path(item.get("filename")) or
                    not isinstance(item.get("status"), str) or item["status"] not in
                    {"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"}):
                reasons.append("invalid_file")
                continue
            name = item["filename"]
            if name in seen:
                reasons.append("duplicate_file")
                continue
            seen.add(name)
            clean = {"filename": name, "status": item["status"], "patch": None}
            if "previous_filename" in item:
                if not _safe_path(item["previous_filename"]):
                    reasons.append("invalid_file")
                    continue
                clean["previous_filename"] = item["previous_filename"]
            if item["status"] == "renamed" and "previous_filename" not in clean:
                reasons.append("invalid_file")
            counts_valid = all(_integer(item.get(key)) for key in ("additions", "deletions", "changes"))
            if not counts_valid:
                reasons.append("invalid_file_counts")
            else:
                clean.update({key: item[key] for key in ("additions", "deletions", "changes")})
                if item["changes"] != item["additions"] + item["deletions"]:
                    reasons.append("invalid_file_counts")
            ignored = any(
                fnmatch.fnmatchcase(candidate, pattern)
                for pattern in result["policy"]["ignorePatterns"]
                for candidate in (name, f"root/{name}")
            )
            patch = item.get("patch")
            if ignored:
                clean["ignored"] = True
            elif stage == "triage":
                pass  # Metadata-only judgment does not require or transmit patches.
            elif not patch:
                reasons.append("missing_patch")  # Includes binary and oversized GitHub patches.
            elif not counts_valid or not _complete_patch(patch, item["additions"], item["deletions"]):
                reasons.append("invalid_patch")
            elif len(patch) > remaining:
                reasons.append("patch_budget")
            else:
                clean["patch"] = patch
                remaining -= len(patch)
            result["files"].append(clean)

    def _document(self, repo, base, path, reasons, *, limit, config=False):
        if not _safe_path(path):
            reasons.append("invalid_config" if config else "invalid_document")
            return None
        data = self._request(f"repos/{repo}/contents/{quote(path, safe='/')}?ref={base}", optional=True)
        if data is _MISSING:
            return None
        invalid = "invalid_config" if config else "invalid_document"
        budget = "invalid_config" if config else "docs_budget"
        if (not isinstance(data, dict) or data.get("type") != "file" or
                data.get("path") != path or data.get("encoding") != "base64" or
                not _integer(data.get("size")) or not isinstance(data.get("content"), str)):
            reasons.append(invalid)
            return None
        if data["size"] > limit or len(data["content"]) > limit * 2 + 1024:
            reasons.append(budget)
            return None
        try:
            raw = base64.b64decode(data["content"].replace("\n", "").replace("\r", ""), validate=True)
            text = raw.decode("utf-8")
        except (ValueError, binascii.Error, UnicodeError):
            reasons.append(invalid)
            return None
        if len(raw) != data["size"] or "\x00" in text:
            reasons.append(invalid)
            return None
        if len(text) > limit:
            reasons.append(budget)
            return None
        return text

    @staticmethod
    def _doc_paths(tree, reasons):
        if (not isinstance(tree, dict) or not isinstance(tree.get("tree"), list) or
                type(tree.get("truncated")) is not bool):
            reasons.append("invalid_tree")
            return []
        if tree["truncated"]:
            reasons.append("truncated_tree")
        paths = set()
        for entry in tree["tree"]:
            if not isinstance(entry, dict) or not _safe_path(entry.get("path")):
                reasons.append("invalid_tree")
                continue
            path = entry["path"]
            name = path.rsplit("/", 1)[-1].lower()
            wanted = (name in {"agents.md", "readme", "readme.md", "readme.rst", "contributing.md"} or
                      (name.startswith(("architecture", "workflow")) and name.endswith((".md", ".rst", ".txt"))) or
                      (path.startswith(".github/workflows/") and name.endswith((".yml", ".yaml"))) or
                      (path.startswith(".github/instructions/") and name.endswith(".instructions.md")))
            if not wanted:
                continue
            if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                reasons.append("invalid_document")
                continue
            paths.add(path)
        return sorted(paths)
