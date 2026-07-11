"""Watched-repository, webhook, registry, state, and status automation.

The CLI module owns argument registration and injects the live review runner.
This module owns local automation state and transport without importing CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List

from . import core, graph_context


def _default_watch_config_path() -> Path:
    return core.artifacts_root().parent / "repos.json"


def _default_watch_state_path() -> Path:
    return core.artifacts_root().parent / "watch-state.json"


def _default_webhook_secret_path() -> Path:
    return core.artifacts_root().parent / "webhook-secret"


def _read_json_file(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = Path(handle.name)
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        _fsync_directory(path.parent)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _normalize_repo_name(repo: str) -> str:
    value = str(repo or "").strip().strip("/")
    if value.startswith("https://github.com/"):
        value = value.removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:").removesuffix(".git").strip("/")
    parts = [part for part in value.split("/") if part]
    if len(parts) != 2:
        raise ValueError("repo must be owner/name")
    return f"{parts[0]}/{parts[1]}"


def _canonical_repo_key(repos: Dict[str, Any], repo: str) -> str:
    return next((str(key) for key in repos if str(key).casefold() == repo.casefold()), repo)


def _ensure_webhook_secret(path_value: str | None) -> tuple[Path, str, bool]:
    raw_path = Path(path_value).expanduser() if path_value else _default_webhook_secret_path()
    path = raw_path.resolve(strict=False)
    created = False
    if raw_path.is_symlink():
        raise ValueError(f"webhook secret file must not be a symlink: {path}")
    if raw_path.exists():
        if not raw_path.is_file():
            raise ValueError(f"webhook secret path is not a regular file: {path}")
        os.chmod(raw_path, 0o600)
        secret = raw_path.read_text(encoding="utf-8").strip()
        if not secret:
            raise ValueError(f"webhook secret file is empty: {path}")
    else:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        secret = secrets.token_urlsafe(48)
        fd = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(secret + "\n")
        except Exception:
            try:
                raw_path.unlink()
            finally:
                raise
        created = True
    return path, secret, created


def _normalize_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _normalize_graph_mode(value: Any) -> str:
    mode = str(value or "off").strip().lower()
    return mode if mode in {"off", "auto", "on"} else "off"


def _load_watch_config(path_value: str | None = None) -> tuple[Path, Dict[str, Any]]:
    path = Path(path_value).expanduser().resolve() if path_value else _default_watch_config_path()
    raw = _read_json_file(path, default={"repos": {}})
    if not isinstance(raw, dict):
        raise ValueError("watch config must be a JSON object")
    repos = raw.get("repos") or {}
    if isinstance(repos, list):
        repos = {str(item.get("repo") or item.get("name") or "").strip(): item for item in repos if isinstance(item, dict)}
    if not isinstance(repos, dict):
        raise ValueError("watch config `repos` must be an object or list")
    normalized: Dict[str, Dict[str, Any]] = {}
    for repo, cfg in repos.items():
        name = str(repo or "").strip()
        if not name or "/" not in name:
            continue
        data = dict(cfg or {}) if isinstance(cfg, dict) else {}
        normalized[name] = {
            "enabled": _normalize_bool(data.get("enabled"), default=True),
            "post_comment": _normalize_bool(data.get("post_comment", data.get("postComment")), default=False),
            "post_findings_only": _normalize_bool(data.get("post_findings_only", data.get("postFindingsOnly")), default=True),
            "review_drafts": _normalize_bool(data.get("review_drafts", data.get("reviewDrafts")), default=False),
            "local_repo": str(data.get("local_repo") or data.get("localRepo") or "").strip() or None,
            "graph_context": _normalize_graph_mode(data.get("graph_context") or data.get("graphContext")),
            "graph_context_binary": str(data.get("graph_context_binary") or data.get("graphContextBinary") or "").strip() or None,
            "mode": str(data.get("mode") or "balanced").strip() or "balanced",
            "max_diff_chars": int(data.get("max_diff_chars") or data.get("maxDiffChars") or 120_000),
        }
    return path, {"repos": normalized}


def _load_watch_state(path_value: str | None = None) -> tuple[Path, Dict[str, Any]]:
    path = Path(path_value).expanduser().resolve() if path_value else _default_watch_state_path()
    raw = _read_json_file(path, default={"schema_version": 1, "reviews": {}})
    if not isinstance(raw, dict):
        raw = {"schema_version": 1, "reviews": {}}
    reviews = raw.get("reviews") if isinstance(raw.get("reviews"), dict) else {}
    return path, {"schema_version": 1, "reviews": reviews}


def _acquire_watch_state_lock(state_path: Path):
    try:
        import fcntl as fcntl_module
    except ImportError as exc:  # pragma: no cover - platform-specific
        raise RuntimeError("watch-run state locking requires fcntl on this platform") from exc
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    handle = lock_path.open("w", encoding="utf-8")
    fcntl_module.flock(handle, fcntl_module.LOCK_EX)
    return handle


def _release_watch_state_lock(handle: Any) -> None:
    try:
        import fcntl as fcntl_module
        fcntl_module.flock(handle, fcntl_module.LOCK_UN)
    finally:
        handle.close()


def _watch_state_key(repo: str, number: int) -> str:
    return f"{repo}#{int(number)}"


_WEBHOOK_REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


def _watch_review_args(repo: str, pr: Dict[str, Any], cfg: Dict[str, Any], *, no_llm: bool) -> argparse.Namespace:
    graph_mode = _normalize_graph_mode(cfg.get("graph_context"))
    return argparse.Namespace(
        pr=f"{repo}#{int(pr['number'])}",
        no_llm=bool(no_llm),
        dry_run=bool(no_llm),
        max_diff_chars=max(1_000, int(cfg.get("max_diff_chars") or 120_000)),
        post_comment=bool(cfg.get("post_comment")) and not no_llm,
        post_findings_only=bool(cfg.get("post_findings_only")),
        allow_truncated_post=False,
        json=True,
        mode=str(cfg.get("mode") or "balanced"),
        graph_context=graph_mode == "on",
        graph_context_auto=graph_mode == "auto",
        local_repo=cfg.get("local_repo"),
        graph_context_binary=cfg.get("graph_context_binary"),
        graph_index_mode="fast",
        max_graph_context_chars=core.MAX_GRAPH_CONTEXT_CHARS,
    )


def _list_open_prs_for_watch(repo: str, *, limit: int) -> List[Dict[str, Any]]:
    data = core.run_gh_json([
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        str(max(1, int(limit))),
        "--search",
        "sort:updated-desc",
        "--json",
        "number,title,headRefOid,isDraft,updatedAt,author,url",
    ])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("number")]


def _watch_result_with_event(result: Dict[str, Any], event: Dict[str, Any] | None) -> Dict[str, Any]:
    return {**result, "event": event} if event else result


def _review_watch_pr(
    repo: str,
    cfg: Dict[str, Any],
    pr: Dict[str, Any],
    *,
    review_runner,
    state: Dict[str, Any],
    state_path: Path,
    now: str,
    current_no_llm: bool,
    force: bool,
    ctx=None,
    event: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    reviews = state.setdefault("reviews", {})
    number = int(pr.get("number") or 0)
    key = _watch_state_key(repo, number)
    head = str(pr.get("headRefOid") or "")
    if pr.get("isDraft") and not cfg.get("review_drafts"):
        return _watch_result_with_event({"repo": repo, "pr": number, "head_sha": head, "action": "skipped", "reason": "draft"}, event)
    previous = reviews.get(key) if isinstance(reviews.get(key), dict) else {}
    previous_satisfies_run = previous.get("success") is True and (current_no_llm or not previous.get("no_llm"))
    if previous.get("head_sha") == head and previous_satisfies_run and not force:
        return _watch_result_with_event({"repo": repo, "pr": number, "head_sha": head, "action": "skipped", "reason": "already_reviewed"}, event)
    review_args = _watch_review_args(repo, pr, cfg, no_llm=current_no_llm)
    started = time.monotonic()
    try:
        payload = review_runner(review_args, ctx=ctx)
    except Exception as exc:
        reviews[key] = {
            "head_sha": head,
            "reviewed_at": now,
            "success": False,
            "no_llm": current_no_llm,
            "error": str(exc),
            "last_event": event,
        }
        _write_json_file(state_path, state)
        return _watch_result_with_event({"repo": repo, "pr": number, "head_sha": head, "success": False, "action": "review_failed", "error": str(exc)}, event)
    elapsed = round(time.monotonic() - started, 3)
    reviewed_head = str(payload.get("head_sha") or head)
    reviews[key] = {
        "head_sha": reviewed_head,
        "listed_head_sha": head if reviewed_head != head else None,
        "reviewed_at": now,
        "success": bool(payload.get("success")),
        "no_llm": current_no_llm,
        "findings": payload.get("findings"),
        "risk": payload.get("risk"),
        "paths": payload.get("paths"),
        "comment": payload.get("comment"),
        "graph_context": payload.get("graph_context"),
        "graph_context_auto_skipped": payload.get("graph_context_auto_skipped"),
        "elapsed_sec": elapsed,
        "last_event": event,
    }
    _write_json_file(state_path, state)
    return _watch_result_with_event({
        "repo": repo,
        "pr": number,
        "url": pr.get("url"),
        "head_sha": reviewed_head,
        "listed_head_sha": head if reviewed_head != head else None,
        "success": bool(payload.get("success")),
        "action": "reviewed",
        "findings": payload.get("findings"),
        "risk": payload.get("risk"),
        "posted_comment": bool(payload.get("comment")) and (payload.get("comment") or {}).get("action") != "skipped",
        "no_llm": current_no_llm,
        "graph_context": payload.get("graph_context"),
        "graph_context_auto_skipped": payload.get("graph_context_auto_skipped"),
        "paths": payload.get("paths"),
        "elapsed_sec": elapsed,
    }, event)


def cmd_watch_run(args: argparse.Namespace, *, review_runner, ctx=None) -> int:
    try:
        config_path, config = _load_watch_config(getattr(args, "config", None))
        state_value = getattr(args, "state", None)
        state_target = Path(state_value).expanduser().resolve() if state_value else _default_watch_state_path()
        lock_handle = _acquire_watch_state_lock(state_target)
        try:
            state_path, state = _load_watch_state(str(state_target))
            wanted = set(getattr(args, "repo", []) or [])
            repos = {
                repo: cfg for repo, cfg in (config.get("repos") or {}).items()
                if cfg.get("enabled") and (not wanted or repo in wanted)
            }
            results: List[Dict[str, Any]] = []
            now = datetime.now(timezone.utc).isoformat()
            current_no_llm = bool(getattr(args, "no_llm", False))
            for repo, cfg in sorted(repos.items()):
                try:
                    prs = _list_open_prs_for_watch(repo, limit=getattr(args, "limit_per_repo", 10))
                except Exception as exc:
                    results.append({"repo": repo, "success": False, "action": "list_failed", "error": str(exc)})
                    continue
                for pr in prs:
                    results.append(_review_watch_pr(
                        repo,
                        cfg,
                        pr,
                        review_runner=review_runner,
                        state=state,
                        state_path=state_path,
                        now=now,
                        current_no_llm=current_no_llm,
                        force=bool(getattr(args, "force", False)),
                        ctx=ctx,
                    ))
            _write_json_file(state_path, state)
        finally:
            _release_watch_state_lock(lock_handle)
        summary = {
            "success": not any(item.get("success") is False for item in results),
            "config": str(config_path),
            "state": str(state_path),
            "repo_count": len(repos),
            "reviewed_count": sum(1 for item in results if item.get("action") == "reviewed"),
            "skipped_count": sum(1 for item in results if item.get("action") == "skipped"),
            "failure_count": sum(1 for item in results if item.get("success") is False),
            "results": results,
        }
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"hermes pr-review watch-run: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print("Hermes PR review watch run")
        print("--------------------------")
        print(f"repos   : {summary['repo_count']}")
        print(f"reviewed: {summary['reviewed_count']}")
        print(f"skipped : {summary['skipped_count']}")
        print(f"failures: {summary['failure_count']}")
        print(f"state   : {summary['state']}")
    return 0 if summary.get("success") else 1


def _load_webhook_payload(path_value: str | None) -> Dict[str, Any]:
    if not path_value or path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).expanduser().read_text(encoding="utf-8")
    data = json.loads(raw or "{}")
    if not isinstance(data, dict):
        raise ValueError("webhook payload must be a JSON object")
    return data


def _github_pull_request_from_payload(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any], str]:
    action = str(payload.get("action") or "").strip()
    repo = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    pull_request = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    full_name = str(repo.get("full_name") or "").strip()
    number = int(pull_request.get("number") or payload.get("number") or 0)
    head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    head_sha = str(head.get("sha") or "").strip()
    if not full_name or not number or not head_sha:
        raise ValueError("pull_request payload must include repository.full_name, pull_request.number, and pull_request.head.sha")
    pr = {
        "number": number,
        "title": pull_request.get("title"),
        "headRefOid": head_sha,
        "isDraft": bool(pull_request.get("draft")),
        "updatedAt": pull_request.get("updated_at"),
        "url": pull_request.get("html_url"),
    }
    return full_name, pr, action


def _fetch_current_pr_for_webhook(repo: str, number: int) -> Dict[str, Any]:
    owner, name = repo.split("/", 1)
    metadata = core.fetch_pr_metadata(core.PullRequestRef(owner=owner, repo=name, number=int(number)))
    return {
        "number": int(metadata.get("number") or number),
        "title": metadata.get("title"),
        "headRefOid": str(metadata.get("headRefOid") or ""),
        "isDraft": bool(metadata.get("isDraft")),
        "updatedAt": metadata.get("updatedAt"),
        "url": metadata.get("url"),
        "state": metadata.get("state"),
    }


def _webhook_event_summary(args: argparse.Namespace, *, review_runner, ctx=None) -> Dict[str, Any]:
    event_type = str(getattr(args, "event", "pull_request") or "pull_request").strip()
    delivery_id = str(getattr(args, "delivery", "") or "").strip() or None
    state_value = getattr(args, "state", None)
    state_target = Path(state_value).expanduser().resolve() if state_value else _default_watch_state_path()
    event_meta = {
        "source": "github_webhook",
        "event": event_type,
        "delivery": delivery_id,
        "action": None,
    }
    config_path: Path | None = None
    result: Dict[str, Any]
    if event_type != "pull_request":
        result = {"action": "ignored", "reason": "unsupported_event", "event": event_meta}
    else:
        payload = _load_webhook_payload(getattr(args, "payload", "-"))
        event_meta["action"] = payload.get("action")
        repo, pr, action = _github_pull_request_from_payload(payload)
        event_meta["action"] = action
        event_meta["repo"] = repo
        event_meta["pr"] = pr.get("number")
        event_meta["head_sha"] = pr.get("headRefOid")
        if action not in _WEBHOOK_REVIEW_ACTIONS:
            result = {"repo": repo, "pr": pr.get("number"), "head_sha": pr.get("headRefOid"), "action": "ignored", "reason": "unsupported_action", "event": event_meta}
        else:
            config_path, config = _load_watch_config(getattr(args, "config", None))
            configured_repos = config.get("repos") or {}
            repo_match = {str(name).casefold(): (name, cfg) for name, cfg in configured_repos.items()}.get(repo.casefold())
            if not repo_match or not repo_match[1].get("enabled"):
                result = {"repo": repo, "pr": pr.get("number"), "head_sha": pr.get("headRefOid"), "action": "ignored", "reason": "repo_not_enabled", "event": event_meta}
            else:
                repo, cfg = repo_match
                event_meta["repo"] = repo
                current_pr = _fetch_current_pr_for_webhook(repo, int(pr.get("number") or 0))
                current_state = str(current_pr.get("state") or "OPEN").upper()
                current_head = str(current_pr.get("headRefOid") or "")
                if current_state != "OPEN":
                    result = {
                        "repo": repo,
                        "pr": pr.get("number"),
                        "head_sha": pr.get("headRefOid"),
                        "current_state": current_state,
                        "action": "ignored",
                        "reason": "pr_not_open",
                        "event": event_meta,
                    }
                elif current_head and current_head != pr.get("headRefOid"):
                    result = {
                        "repo": repo,
                        "pr": pr.get("number"),
                        "head_sha": pr.get("headRefOid"),
                        "current_head_sha": current_head,
                        "action": "ignored",
                        "reason": "stale_payload_head",
                        "event": event_meta,
                    }
                else:
                    if current_head:
                        pr = current_pr
                    lock_handle = _acquire_watch_state_lock(state_target)
                    try:
                        state_path, state = _load_watch_state(str(state_target))
                        now = datetime.now(timezone.utc).isoformat()
                        result = _review_watch_pr(
                            repo,
                            cfg,
                            pr,
                            review_runner=review_runner,
                            state=state,
                            state_path=state_path,
                            now=now,
                            current_no_llm=bool(getattr(args, "no_llm", False)),
                            force=bool(getattr(args, "force", False)),
                            ctx=ctx,
                            event=event_meta,
                        )
                    finally:
                        _release_watch_state_lock(lock_handle)
    return {
        "success": result.get("success") is not False,
        "config": str(config_path) if config_path else None,
        "state": str(state_target),
        "result": result,
    }


def cmd_webhook_event(args: argparse.Namespace, *, review_runner, ctx=None) -> int:
    try:
        summary = _webhook_event_summary(args, review_runner=review_runner, ctx=ctx)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"hermes pr-review webhook-event: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        result = summary["result"]
        print("Hermes PR review webhook event")
        print("------------------------------")
        print(f"action : {result.get('action')}")
        if result.get("repo"):
            print(f"repo   : {result.get('repo')}")
            print(f"pr     : {result.get('pr')}")
        if result.get("reason"):
            print(f"reason : {result.get('reason')}")
        print(f"state  : {summary['state']}")
    return 0 if summary.get("success") else 1


def _resolve_webhook_secret(args: argparse.Namespace) -> str:
    if getattr(args, "secret", None) is not None:
        value = str(getattr(args, "secret")).strip()
        if value:
            return value
        raise ValueError("webhook secret must not be empty")
    if getattr(args, "secret_file", None):
        value = Path(getattr(args, "secret_file")).expanduser().read_text(encoding="utf-8").strip()
        if value:
            return value
        raise ValueError("webhook secret file must not be empty")
    env_name = str(getattr(args, "secret_env", None) or "HERMES_PR_REVIEW_WEBHOOK_SECRET")
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    raise ValueError(f"missing webhook secret; pass --secret-file or set {env_name}")


def _valid_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _webhook_delivery_spool_dir() -> Path:
    return core.artifacts_root().parent / "deliveries"


def _webhook_delivery_spool_path(delivery: str | None, body: bytes) -> Path:
    key = delivery or hashlib.sha256(body).hexdigest()[:24]
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in key)[:96]
    return _webhook_delivery_spool_dir() / f"{safe}.json"


def _write_webhook_delivery_spool(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_file(path, payload)


def _create_webhook_delivery_spool(path: Path, payload: Dict[str, Any]) -> bool:
    """Atomically create a delivery record without replacing an existing delivery ID."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{secrets.token_hex(8)}.admission"
    try:
        _write_json_file(temp_path, payload)
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
        return True
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        else:
            _fsync_directory(path.parent)


def _acquire_delivery_recovery_lock(spool_path: Path, *, blocking: bool = False):
    try:
        import fcntl as fcntl_module
    except ImportError as exc:  # pragma: no cover - platform-specific
        raise RuntimeError("webhook delivery recovery requires fcntl on this platform") from exc
    handle = spool_path.open("r+", encoding="utf-8")
    try:
        os.chmod(spool_path, 0o600)
        flags = fcntl_module.LOCK_EX if blocking else fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
        fcntl_module.flock(handle, flags)
    except BlockingIOError:
        handle.close()
        return None
    except Exception:
        handle.close()
        raise
    return handle


def _release_advisory_lock(handle: Any) -> None:
    try:
        import fcntl as fcntl_module
        fcntl_module.flock(handle, fcntl_module.LOCK_UN)
    finally:
        handle.close()


def _acquire_webhook_processing_lock(*, blocking: bool):
    try:
        import fcntl as fcntl_module
    except ImportError as exc:  # pragma: no cover - platform-specific
        raise RuntimeError("webhook processing requires fcntl on this platform") from exc
    lock_path = _webhook_delivery_spool_dir() / ".processing.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "w", encoding="utf-8")
    try:
        os.chmod(lock_path, 0o600)
        flags = fcntl_module.LOCK_EX if blocking else fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
        fcntl_module.flock(handle, flags)
    except BlockingIOError:
        handle.close()
        return None
    except Exception:
        handle.close()
        raise
    return handle


def _status_level(items: List[Dict[str, Any]]) -> str:
    levels = {str(item.get("status") or "ok") for item in items}
    if "fail" in levels:
        return "fail"
    if "warn" in levels:
        return "warn"
    return "ok"


def _secret_status(path_value: str | None) -> Dict[str, Any]:
    raw_path = Path(path_value).expanduser() if path_value else _default_webhook_secret_path()
    path = raw_path.resolve(strict=False)
    info: Dict[str, Any] = {"path": str(path), "exists": raw_path.exists()}
    try:
        if raw_path.is_symlink():
            return {**info, "status": "fail", "reason": "secret path is a symlink"}
        if not raw_path.exists():
            return {**info, "status": "warn", "reason": "secret file missing; run enable or create one before configuring GitHub"}
        if not raw_path.is_file():
            return {**info, "status": "fail", "reason": "secret path is not a regular file"}
        mode = raw_path.stat().st_mode & 0o777
        text = raw_path.read_text(encoding="utf-8").strip()
        status = "ok"
        reasons: List[str] = []
        if not text:
            status = "fail"
            reasons.append("secret file is empty")
        if mode & 0o077:
            status = "warn" if status == "ok" else status
            reasons.append(f"permissions are {oct(mode)}; expected 0o600 or stricter")
        return {**info, "status": status, "mode": oct(mode), "present": bool(text), "reason": "; ".join(reasons) or None}
    except Exception as exc:
        return {**info, "status": "fail", "reason": str(exc)}


def _receiver_status(url: str, *, timeout: float) -> Dict[str, Any]:
    try:
        started = time.monotonic()
        with urllib.request.urlopen(url, timeout=max(0.1, float(timeout))) as response:
            raw = response.read(16_384)
            elapsed = round(time.monotonic() - started, 3)
            payload: Any
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = raw.decode("utf-8", errors="replace")[:500]
            status_code = int(getattr(response, "status", 0) or 0)
            ok = 200 <= status_code < 300
            return {"status": "ok" if ok else "warn", "url": url, "http_status": status_code, "elapsed_sec": elapsed, "body": payload}
    except Exception as exc:
        return {"status": "warn", "url": url, "reason": str(exc)}


def _github_hook_delivery_status(repo: str | None, hook_id: str | None, *, recent_limit: int) -> Dict[str, Any]:
    repo_value = (repo or "").strip()
    hook_value = str(hook_id or "").strip()
    if not repo_value and not hook_value:
        return {"status": "skipped", "reason": "no GitHub hook configured for status probe"}
    if not repo_value or not hook_value:
        return {"status": "warn", "repo": repo_value or None, "hook_id": hook_value or None, "reason": "both --github-repo and --github-hook-id are required"}
    bounded_limit = min(100, max(1, int(recent_limit)))
    try:
        completed = subprocess.run(
            ["gh", "api", f"repos/{repo_value}/hooks/{hook_value}/deliveries", "-X", "GET", "-f", f"per_page={bounded_limit}"],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {"status": "warn", "repo": repo_value, "hook_id": hook_value, "reason": str(exc)}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {"status": "warn", "repo": repo_value, "hook_id": hook_value, "reason": detail[:500] or f"gh api exited {completed.returncode}"}
    try:
        payload = json.loads(completed.stdout or "[]")
    except Exception as exc:
        return {"status": "warn", "repo": repo_value, "hook_id": hook_value, "reason": f"invalid gh delivery JSON: {exc}"}
    deliveries: List[Any] = list(payload) if isinstance(payload, list) else []
    recent: List[Dict[str, Any]] = []
    for delivery in deliveries[:bounded_limit]:
        if not isinstance(delivery, dict):
            continue
        recent.append({
            "id": delivery.get("id"),
            "event": delivery.get("event"),
            "action": delivery.get("action"),
            "status": delivery.get("status"),
            "status_code": delivery.get("status_code"),
            "delivered_at": delivery.get("delivered_at"),
            "redelivery": delivery.get("redelivery"),
        })
    latest = recent[0] if recent else None
    status = "ok"
    reason = None
    if not recent:
        status = "warn"
        reason = "no GitHub hook deliveries returned"
    else:
        try:
            status_code = int(latest.get("status_code") or 0)
        except (TypeError, ValueError):
            status_code = 0
        if latest.get("status") != "OK" or not (200 <= status_code < 300):
            status = "warn"
            reason = f"latest delivery is {latest.get('status')} HTTP {latest.get('status_code')}"
    return {"status": status, "repo": repo_value, "hook_id": hook_value, "latest": latest, "recent": recent, "total_returned": len(deliveries), "reason": reason}


def _delivery_status(path_value: str | None, *, recent_limit: int) -> Dict[str, Any]:
    path = Path(path_value).expanduser().resolve() if path_value else _webhook_delivery_spool_dir()
    if not path.exists():
        return {"status": "warn", "path": str(path), "exists": False, "total": 0, "counts": {}, "recent": [], "reason": "no deliveries recorded yet"}
    if not path.is_dir():
        return {"status": "fail", "path": str(path), "exists": True, "reason": "deliveries path is not a directory"}
    files = sorted(path.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    counts: Dict[str, int] = {}
    recent: List[Dict[str, Any]] = []
    bad_files = 0
    for item in files:
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
            delivery_status = str(payload.get("status") or "unknown") if isinstance(payload, dict) else "invalid"
            counts[delivery_status] = counts.get(delivery_status, 0) + 1
            if len(recent) < max(0, int(recent_limit)):
                result_value = payload.get("result")
                result: Dict[str, Any] = result_value if isinstance(result_value, dict) else {}
                nested_value = result.get("result")
                nested_result: Dict[str, Any] = nested_value if isinstance(nested_value, dict) else {}
                result_action = nested_result.get("action") or result.get("action")
                result_reason = nested_result.get("reason") or result.get("reason")
                recent.append({
                    "file": str(item),
                    "status": delivery_status,
                    "event": payload.get("event"),
                    "delivery": payload.get("delivery"),
                    "accepted_at": payload.get("accepted_at"),
                    "processed_at": payload.get("processed_at"),
                    "rc": payload.get("rc"),
                    "result_action": result_action,
                    "result_reason": result_reason,
                })
        except Exception:
            bad_files += 1
    status = "warn" if counts.get("failed") or counts.get("accepted") or bad_files else "ok"
    return {"status": status, "path": str(path), "exists": True, "total": len(files), "counts": counts, "bad_files": bad_files, "recent": recent}


def _graph_setup_command(local_repo: str | None, graph_binary: str | None = None) -> str | None:
    if not local_repo:
        return None
    binary_option = f" --graph-context-binary {shlex.quote(str(graph_binary))}" if graph_binary else ""
    return f"hermes pr-review graph-setup --local-repo {shlex.quote(str(local_repo))}{binary_option} --install-missing"


def _repo_graph_health(cfg: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(cfg.get("graph_context") or "off").strip().lower() or "off"
    local_repo = str(cfg.get("local_repo") or "").strip() or None
    result: Dict[str, Any] = {
        "enabled": mode in {"auto", "on"},
        "mode": mode,
        "local_repo": local_repo,
        "healthy": None,
        "status": "skipped",
        "reason": "graph context disabled" if mode == "off" else None,
        "next_step": None,
    }
    if mode not in {"auto", "on"}:
        return result
    setup_command = _graph_setup_command(local_repo, cfg.get("graph_context_binary"))
    result["setup_command"] = setup_command
    if not local_repo:
        result.update({
            "healthy": False,
            "status": "warn",
            "reason": "local repo not configured",
            "next_step": "set localRepo with: hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout",
        })
        return result
    try:
        health = graph_context.codegraph_health(
            local_repo=local_repo,
            binary=cfg.get("graph_context_binary"),
            sync=False,
        )
    except Exception as exc:
        result.update({"healthy": False, "status": "warn", "reason": str(exc), "next_step": setup_command})
        return result
    status_value = health.get("status")
    index_value = health.get("index")
    status: Dict[str, Any] = status_value if isinstance(status_value, dict) else {}
    index: Dict[str, Any] = index_value if isinstance(index_value, dict) else {}
    next_step = None if health.get("healthy") else setup_command
    if health.get("reason") == "checkout has non-CodeGraph dirty paths":
        checkout_value = health.get("checkout")
        checkout: Dict[str, Any] = checkout_value if isinstance(checkout_value, dict) else {}
        dirty = checkout.get("dirty_paths") or []
        preview = ", ".join(str(path) for path in dirty[:3])
        next_step = "commit, stash, or revert non-CodeGraph checkout changes" + (f" ({preview})" if preview else "")
    result.update(
        {
            "healthy": bool(health.get("healthy")),
            "status": "ok" if health.get("healthy") else "warn",
            "reason": health.get("reason"),
            "provider": health.get("provider"),
            "binary_name": health.get("binary_name"),
            "head": health.get("head"),
            "index": {
                "exists": bool(index.get("exists")),
                "initialized": bool(status.get("initialized")),
                "files": status.get("fileCount"),
                "nodes": status.get("nodeCount"),
                "edges": status.get("edgeCount"),
                "languages": status.get("languages") or [],
                "pending_changes": status.get("pendingChanges") or {},
                "reindex_recommended": status.get("reindexRecommended"),
            },
            "checkout": health.get("checkout"),
            "next_step": next_step,
        }
    )
    return result


def _repo_live_graph_status(cfg: Dict[str, Any], last_review: Dict[str, Any] | None) -> Dict[str, Any]:
    mode = _normalize_graph_mode(cfg.get("graph_context"))
    if mode not in {"auto", "on"}:
        return {"status": "skipped", "enabled": False, "reason": "graph context disabled"}
    if not isinstance(last_review, dict):
        return {"status": "skipped", "enabled": True, "reason": "no completed live review recorded"}
    if last_review.get("success") is False:
        return {
            "status": "warn",
            "enabled": True,
            "used": False,
            "reason": "latest live review failed",
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    if last_review.get("no_llm") is True:
        return {
            "status": "skipped",
            "enabled": True,
            "used": None,
            "reason": "latest graph collection was a --no-llm smoke, not a completed model review",
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    if last_review.get("success") is not True or last_review.get("no_llm") is not False:
        return {
            "status": "skipped",
            "enabled": True,
            "used": None,
            "reason": "latest review predates live review outcome tracking",
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    fallback_reason = str(last_review.get("graph_context_auto_skipped") or "").strip()
    graph_value = last_review.get("graph_context")
    graph = graph_value if isinstance(graph_value, dict) else {}
    if fallback_reason:
        return {
            "status": "warn",
            "enabled": True,
            "used": False,
            "reason": fallback_reason,
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    graph_status = str(graph.get("status") or "").strip().lower()
    if graph.get("enabled") and graph_status == "collected":
        return {
            "status": "ok",
            "enabled": True,
            "used": True,
            "provider": graph.get("provider"),
            "graph_status": graph.get("status"),
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    if graph.get("enabled") and graph_status:
        return {
            "status": "warn",
            "enabled": True,
            "used": False,
            "provider": graph.get("provider"),
            "graph_status": graph.get("status"),
            "reason": f"latest review graph context status was {graph.get('status')}",
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    if graph.get("enabled"):
        return {
            "status": "skipped",
            "enabled": True,
            "used": None,
            "reason": "latest review graph outcome is missing collection status",
            "reviewed_at": last_review.get("reviewed_at"),
            "head_sha": last_review.get("head_sha"),
        }
    return {
        "status": "skipped",
        "enabled": True,
        "used": None,
        "reason": "latest review predates live graph outcome tracking",
        "reviewed_at": last_review.get("reviewed_at"),
        "head_sha": last_review.get("head_sha"),
    }


def _repo_status_rows(config: Dict[str, Any], state: Dict[str, Any], wanted: List[str]) -> List[Dict[str, Any]]:
    """Build display rows from the normalized output of _load_watch_config()."""
    reviews = state.get("reviews") if isinstance(state.get("reviews"), dict) else {}
    assert isinstance(reviews, dict)
    wanted_set = set(wanted or [])
    rows: List[Dict[str, Any]] = []
    for repo, cfg in sorted((config.get("repos") or {}).items()):
        if wanted_set and repo not in wanted_set:
            continue
        repo_reviews = {key: value for key, value in reviews.items() if str(key).startswith(f"{repo}#") and isinstance(value, dict)}
        failures = sum(1 for value in repo_reviews.values() if value.get("success") is False)
        successes = sum(1 for value in repo_reviews.values() if value.get("success") is True)
        last_key = None
        last_review = None
        if repo_reviews:
            last_key, last_review = max(repo_reviews.items(), key=lambda item: str(item[1].get("reviewed_at") or ""))
        graph_health = _repo_graph_health(cfg)
        live_graph = _repo_live_graph_status(cfg, last_review)
        rows.append({
            "repo": repo,
            "enabled": bool(cfg.get("enabled")),
            "post_comment": bool(cfg.get("post_comment")),
            "post_findings_only": bool(cfg.get("post_findings_only")),
            "review_drafts": bool(cfg.get("review_drafts")),
            "graph_context": cfg.get("graph_context"),
            "graph_context_binary": cfg.get("graph_context_binary"),
            "graph_health": graph_health,
            "live_graph": live_graph,
            "local_repo": cfg.get("local_repo"),
            "mode": cfg.get("mode"),
            "max_diff_chars": cfg.get("max_diff_chars"),
            "review_count": len(repo_reviews),
            "success_count": successes,
            "failure_count": failures,
            "last_review_key": last_key,
            "last_review": last_review,
        })
    return rows


def _status_summary(args: argparse.Namespace) -> Dict[str, Any]:
    config_path, config = _load_watch_config(getattr(args, "config", None))
    state_path, state = _load_watch_state(getattr(args, "state", None))
    wanted = list(getattr(args, "repo", []) or [])
    repos = _repo_status_rows(config, state, wanted)
    config_status = "ok" if repos else "warn"
    config_reason = None if repos else "no enabled repositories matched" if wanted else "no repositories configured"
    checks = [
        {"name": "config", "status": config_status, "path": str(config_path), "repo_count": len(repos), "reason": config_reason},
        {"name": "secret", **_secret_status(getattr(args, "secret_file", None))},
        {"name": "state", "status": "ok" if state_path.exists() else "warn", "path": str(state_path), "exists": state_path.exists(), "review_count": len(state.get("reviews") or {})},
        {"name": "deliveries", **_delivery_status(getattr(args, "deliveries_dir", None), recent_limit=getattr(args, "recent_deliveries", 5))},
        {"name": "github_hook", **_github_hook_delivery_status(getattr(args, "github_repo", None), getattr(args, "github_hook_id", None), recent_limit=getattr(args, "github_deliveries", 5))},
    ]
    graph_warnings = [row for row in repos if ((row.get("graph_health") or {}).get("status") == "warn")]
    if graph_warnings:
        checks.append({"name": "graph", "status": "warn", "repo_count": len(graph_warnings), "reason": "one or more graph-enabled repos are not ready"})
    elif any(((row.get("graph_health") or {}).get("enabled")) for row in repos):
        checks.append({"name": "graph", "status": "ok", "repo_count": 0, "reason": None})
    else:
        checks.append({"name": "graph", "status": "skipped", "reason": "no graph-enabled repos"})
    live_graph_warnings = [row for row in repos if ((row.get("live_graph") or {}).get("status") == "warn")]
    live_graph_ok = [row for row in repos if ((row.get("live_graph") or {}).get("status") == "ok")]
    if live_graph_warnings:
        checks.append({"name": "graph_live", "status": "warn", "repo_count": len(live_graph_warnings), "reason": "one or more latest live reviews fell back from graph context"})
    elif live_graph_ok:
        checks.append({"name": "graph_live", "status": "ok", "repo_count": len(live_graph_ok), "reason": None})
    else:
        checks.append({"name": "graph_live", "status": "skipped", "reason": "no tracked live graph outcome"})
    if bool(getattr(args, "skip_receiver", False)):
        checks.append({"name": "receiver", "status": "skipped", "reason": "--skip-receiver"})
    else:
        checks.append({"name": "receiver", **_receiver_status(str(getattr(args, "receiver_url", "") or "http://127.0.0.1:8787/healthz"), timeout=getattr(args, "receiver_timeout", 2.0))})
    actionable = [item for item in checks if item.get("status") != "skipped"]
    summary = {
        "success": _status_level(actionable) != "fail",
        "status": _status_level(actionable),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "config": str(config_path),
        "state": str(state_path),
        "repo_count": len(repos),
        "enabled_repo_count": sum(1 for row in repos if row.get("enabled")),
        "repos": repos,
        "checks": checks,
    }
    summary["next_steps"] = _status_next_steps(summary)
    return summary


def _status_next_steps(summary: Dict[str, Any]) -> List[str]:
    checks = {str(check.get("name")): check for check in summary.get("checks", []) if isinstance(check, dict)}
    steps: List[str] = []
    if not summary.get("repo_count"):
        steps.append("Add a repo with: hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout")
    elif not summary.get("enabled_repo_count"):
        steps.append("Enable at least one repo in the registry before expecting watch-run/webhooks to review PRs")

    secret = checks.get("secret") or {}
    if secret.get("status") == "fail":
        steps.append(f"Fix the webhook secret path before serving webhooks: {secret.get('reason')}")
    elif secret.get("status") == "warn" and summary.get("repo_count"):
        steps.append("Create the webhook secret via: hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout")

    receiver = checks.get("receiver") or {}
    if receiver.get("status") == "warn":
        steps.append("Start the local receiver with: hermes pr-review webhook-serve --host 127.0.0.1 --port 8787")

    state = checks.get("state") or {}
    if state.get("status") == "warn" and summary.get("repo_count"):
        steps.append("Run a first smoke pass with: hermes pr-review watch-run --no-llm --json")

    deliveries = checks.get("deliveries") or {}
    if deliveries.get("status") == "warn" and deliveries.get("counts", {}).get("failed"):
        steps.append("Inspect failed delivery spool files under the deliveries path before enabling posting")
    elif deliveries.get("status") == "warn" and summary.get("repo_count"):
        steps.append("After the receiver is running, send a GitHub webhook ping and re-run status to confirm deliveries are spooled")

    github_hook = checks.get("github_hook") or {}
    if github_hook.get("status") == "warn":
        steps.append("Inspect GitHub webhook deliveries with: gh api repos/OWNER/REPO/hooks/HOOK_ID/deliveries")

    for row in summary.get("repos", []):
        graph = row.get("graph_health") if isinstance(row, dict) else None
        if isinstance(graph, dict) and graph.get("status") == "warn":
            next_step = graph.get("next_step") or graph.get("setup_command")
            if next_step:
                steps.append(f"Prepare graph context for {row.get('repo')}: {next_step}")
            else:
                steps.append(f"Inspect graph context for {row.get('repo')}: {graph.get('reason')}")
        live_graph = row.get("live_graph") if isinstance(row, dict) else None
        if isinstance(live_graph, dict) and live_graph.get("status") == "warn":
            reason = str(live_graph.get("reason") or "unknown live graph fallback")
            lowered_reason = reason.lower()
            runtime_markers = (
                "binary not found",
                "not executable",
                "command not found",
                "no such file or directory",
                "permission denied",
                "env: node",
                "node: not found",
            )
            if any(marker in lowered_reason for marker in runtime_markers):
                steps.append(
                    f"Fix live graph runtime for {row.get('repo')}: {reason}; "
                    f"set a stable launcher with `hermes pr-review enable {row.get('repo')} --graph-context-binary /path/to/codegraph`"
                )
            else:
                local_repo = row.get("local_repo")
                graph_binary = row.get("graph_context_binary")
                binary_option = f" --graph-context-binary {shlex.quote(str(graph_binary))}" if graph_binary else ""
                health_command = (
                    f"hermes pr-review graph-health --local-repo {shlex.quote(str(local_repo))}{binary_option} --json"
                    if local_repo
                    else f"hermes pr-review status --repo {row.get('repo')} --json"
                )
                steps.append(
                    f"Inspect live graph fallback for {row.get('repo')}: {reason}; "
                    f"run `{health_command}` and trigger a fresh review after correcting the reported condition"
                )

    if not steps and summary.get("status") == "ok":
        steps.append("Status is clean; run hermes pr-review watch-run --json or keep the webhook receiver running")
    return steps


def cmd_status(args: argparse.Namespace) -> int:
    try:
        summary = _status_summary(args)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "status": "fail", "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"hermes pr-review status: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print("Hermes PR review status")
        print("-----------------------")
        print(f"status  : {summary['status']}")
        print(f"repos   : {summary['enabled_repo_count']} enabled / {summary['repo_count']} listed")
        for check in summary["checks"]:
            reason = f" ({check.get('reason')})" if check.get("reason") else ""
            print(f"{check['name']:<10}: {check.get('status')}{reason}")
        if summary["repos"]:
            print("\nRepositories:")
            for row in summary["repos"]:
                posting = "post" if row.get("post_comment") else "no-post"
                if row.get("post_comment") and row.get("post_findings_only"):
                    posting = "post/findings-only"
                last = row.get("last_review_key") or "no reviews yet"
                graph = row.get("graph_health") or {}
                live_graph = row.get("live_graph") or {}
                graph_label = str(row.get("graph_context") or "off")
                if graph.get("enabled"):
                    if graph.get("healthy"):
                        graph_label = f"{graph.get('mode')} ready via {graph.get('provider') or 'graph'}"
                    else:
                        graph_label = f"{graph.get('mode')} not ready: {graph.get('reason')}"
                print(f"  - {row['repo']} [{posting}, graph={graph_label}] last={last}")
                if live_graph.get("status") == "warn":
                    print(f"    live graph: fallback ({live_graph.get('reason')})")
                elif live_graph.get("status") == "ok":
                    print(f"    live graph: used via {live_graph.get('provider') or 'graph'}")
                if graph.get("enabled") and graph.get("next_step"):
                    print(f"    graph next: {graph.get('next_step')}")
        if summary.get("next_steps"):
            print("\nNext steps:")
            for step in summary["next_steps"]:
                print(f"  - {step}")
    return 0 if summary.get("success") else 1


def _run_webhook_event_from_http(
    *,
    review_runner,
    body: bytes,
    event: str,
    delivery: str | None,
    config: str | None,
    state: str | None,
    force: bool,
    no_llm: bool,
    ctx=None,
) -> tuple[int, Dict[str, Any]]:
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as handle:
        handle.write(body)
        payload_path = handle.name
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=config,
        state=state,
        payload=payload_path,
        event=event,
        delivery=delivery,
        force=force,
        no_llm=no_llm,
        json=True,
    )
    try:
        payload = _webhook_event_summary(args, review_runner=review_runner, ctx=ctx)
        return 0 if payload.get("success") else 1, payload
    finally:
        try:
            Path(payload_path).unlink()
        except FileNotFoundError:
            pass


def _wait_for_webhook_recovery_retry(stop_event: threading.Event, seconds: float) -> bool:
    return stop_event.wait(seconds)


def _recover_accepted_webhook_deliveries(
    *,
    review_runner,
    config: str | None,
    state: str | None,
    no_llm: bool,
    processing_lock: threading.Lock,
    processing_slot: threading.BoundedSemaphore,
    spool_paths: List[Path] | None = None,
    should_stop: Callable[[], bool] | None = None,
    ctx=None,
) -> Dict[str, int]:
    summary = {"accepted": 0, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    spool_dir = _webhook_delivery_spool_dir()
    if spool_paths is None and not spool_dir.exists():
        return summary
    candidates = sorted(spool_paths) if spool_paths is not None else sorted(spool_dir.glob("*.json"))
    for spool_path in candidates:
        if should_stop is not None and should_stop():
            break
        try:
            spool_payload = _read_json_file(spool_path, default={})
        except Exception as exc:
            summary["unreadable"] += 1
            print(
                json.dumps({"action": "recovery_unreadable", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
            continue
        if not isinstance(spool_payload, dict) or spool_payload.get("status") != "accepted":
            continue
        summary["accepted"] += 1
        try:
            lock_handle = _acquire_delivery_recovery_lock(spool_path)
        except FileNotFoundError:
            print(
                json.dumps({"action": "recovery_missing", "spool": str(spool_path)}, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
            continue
        except Exception as exc:
            summary["lock_errors"] += 1
            print(
                json.dumps({"action": "recovery_failed", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
            continue
        if lock_handle is None:
            summary["locked"] += 1
            continue
        processing_handle = None
        slot_acquired = False
        try:
            if not processing_slot.acquire(blocking=False):
                summary["locked"] += 1
                continue
            slot_acquired = True
            processing_handle = _acquire_webhook_processing_lock(blocking=False)
            if processing_handle is None:
                summary["locked"] += 1
                continue
            current = _read_json_file(spool_path, default={})
            if not isinstance(current, dict) or current.get("status") != "accepted":
                continue
            body_value = current.get("body")
            event = str(current.get("event") or "").strip()
            delivery = str(current.get("delivery") or "").strip() or None
            started_at = datetime.now(timezone.utc).isoformat()
            rc = 1
            result: Dict[str, Any]
            if not isinstance(body_value, str):
                result = {"success": False, "error": "accepted delivery body must be a string", "event": event or None, "delivery": delivery}
            elif not event:
                result = {"success": False, "error": "accepted delivery event is missing", "event": None, "delivery": delivery}
            else:
                try:
                    with processing_lock:
                        rc, result = _run_webhook_event_from_http(
                            review_runner=review_runner,
                            body=body_value.encode("utf-8"),
                            event=event,
                            delivery=delivery,
                            config=config,
                            state=state,
                            force=False,
                            no_llm=no_llm,
                            ctx=ctx,
                        )
                except Exception as exc:
                    print(
                        json.dumps({"action": "recovery_event_retryable", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                        file=sys.stderr,
                        flush=True,
                    )
                    raise
            completed = {
                **current,
                "status": "processed" if rc == 0 else "failed",
                "recovered": True,
                "recovery_started_at": started_at,
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "rc": rc,
                "result": result,
            }
            _write_webhook_delivery_spool(spool_path, completed)
            summary["recovered" if rc == 0 else "failed"] += 1
            print(
                json.dumps(
                    {"action": "recovered", "event": event or None, "delivery": delivery, "spool": str(spool_path), "rc": rc, "result": result},
                    sort_keys=True,
                    default=str,
                ),
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            summary["retryable"] += 1
            print(
                json.dumps({"action": "recovery_retryable", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
        finally:
            if processing_handle is not None:
                try:
                    _release_advisory_lock(processing_handle)
                except Exception as exc:
                    summary["lock_errors"] += 1
                    print(
                        json.dumps({"action": "recovery_processing_unlock_error", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                        file=sys.stderr,
                        flush=True,
                    )
            if slot_acquired:
                processing_slot.release()
            try:
                _release_advisory_lock(lock_handle)
            except Exception as exc:
                summary["lock_errors"] += 1
                print(
                    json.dumps({"action": "recovery_unlock_error", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                    file=sys.stderr,
                    flush=True,
                )
    return summary


def _prequeue_ignored_webhook_summary(*, body: bytes, event: str, delivery: str | None, state: str | None) -> Dict[str, Any] | None:
    """Return an ignored webhook-event summary when a delivery can skip the review queue.

    This runs only after HMAC verification. It intentionally classifies events and
    pull_request actions that can never trigger a review before acquiring the
    single review slot, so low-value GitHub deliveries do not block real review
    work while a previous PR review is running.
    """
    event_type = (event or "").strip()
    state_target = Path(state).expanduser().resolve() if state else _default_watch_state_path()
    event_meta: Dict[str, Any] = {
        "source": "github_webhook",
        "event": event_type,
        "delivery": delivery or None,
        "action": None,
    }
    if event_type != "pull_request":
        return {
            "success": True,
            "config": None,
            "state": str(state_target),
            "result": {"action": "ignored", "reason": "unsupported_event", "event": event_meta},
        }
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    action = str(payload.get("action") or "").strip()
    if action in _WEBHOOK_REVIEW_ACTIONS:
        return None
    event_meta["action"] = action
    repo_value = payload.get("repository")
    repo: Dict[str, Any] = repo_value if isinstance(repo_value, dict) else {}
    pull_request_value = payload.get("pull_request")
    pull_request: Dict[str, Any] = pull_request_value if isinstance(pull_request_value, dict) else {}
    head_value = pull_request.get("head")
    head: Dict[str, Any] = head_value if isinstance(head_value, dict) else {}
    repo_name = str(repo.get("full_name") or "").strip() or None
    pr_number = pull_request.get("number") or payload.get("number")
    head_sha = str(head.get("sha") or "").strip() or None
    if repo_name:
        event_meta["repo"] = repo_name
    if pr_number:
        event_meta["pr"] = pr_number
    if head_sha:
        event_meta["head_sha"] = head_sha
    result: Dict[str, Any] = {
        "repo": repo_name,
        "pr": pr_number,
        "head_sha": head_sha,
        "action": "ignored",
        "reason": "unsupported_action",
        "event": event_meta,
    }
    return {"success": True, "config": None, "state": str(state_target), "result": result}


def cmd_webhook_serve(args: argparse.Namespace, *, review_runner, ctx=None) -> int:
    try:
        secret = _resolve_webhook_secret(args)
        host = str(getattr(args, "host", "127.0.0.1") or "127.0.0.1")
        port = int(getattr(args, "port", 8787) or 8787)
        path = str(getattr(args, "path", "/webhooks/github") or "/webhooks/github")
        if not path.startswith("/"):
            path = "/" + path
        max_body_bytes = max(1, int(getattr(args, "max_body_bytes", 1_000_000) or 1_000_000))
        read_timeout = max(1.0, float(getattr(args, "read_timeout", 10.0) or 10.0))
        config = getattr(args, "config", None)
        state = getattr(args, "state", None)
        force = bool(getattr(args, "force", False))
        no_llm = bool(getattr(args, "no_llm", False))
        once = bool(getattr(args, "once", False))
        processing_lock = threading.Lock()
        processing_slot = threading.BoundedSemaphore(1)

        class GitHubWebhookHandler(BaseHTTPRequestHandler):
            server_version = "HermesPRReviewWebhook/1.0"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(read_timeout)

            def log_message(self, format: str, *values: Any) -> None:  # noqa: A002 - stdlib signature
                print(f"{self.address_string()} - {format % values}", file=sys.stderr)

            def _send_json(self, status: int, payload: Dict[str, Any], *, include_body: bool = True) -> None:
                raw = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw) if include_body else 0))
                self.end_headers()
                if include_body:
                    self.wfile.write(raw)

            def do_HEAD(self) -> None:
                request_path = self.path.split("?", 1)[0]
                if request_path == "/healthz":
                    self._send_json(200, {"success": True, "service": "hermes-pr-review-webhook"}, include_body=False)
                else:
                    self._send_json(404, {"success": False, "error": "not_found"}, include_body=False)
                    setattr(self.server, "last_request_success", False)

            def do_GET(self) -> None:
                request_path = self.path.split("?", 1)[0]
                if request_path == "/healthz":
                    self._send_json(200, {"success": True, "service": "hermes-pr-review-webhook"})
                else:
                    self._send_json(404, {"success": False, "error": "not_found"})
                    setattr(self.server, "last_request_success", False)

            def do_POST(self) -> None:
                request_path = self.path.split("?", 1)[0]
                if request_path != path:
                    self._send_json(404, {"success": False, "error": "not_found"})
                    setattr(self.server, "last_request_success", False)
                    return
                length_header = self.headers.get("Content-Length")
                try:
                    length = int(length_header or "0")
                except ValueError:
                    self._send_json(400, {"success": False, "error": "invalid_content_length"})
                    setattr(self.server, "last_request_success", False)
                    return
                if length <= 0:
                    self._send_json(400, {"success": False, "error": "empty_body"})
                    setattr(self.server, "last_request_success", False)
                    return
                if length > max_body_bytes:
                    self._send_json(413, {"success": False, "error": "body_too_large", "max_body_bytes": max_body_bytes})
                    setattr(self.server, "last_request_success", False)
                    return
                try:
                    body = self.rfile.read(length)
                except socket.timeout:
                    self._send_json(408, {"success": False, "error": "request_timeout"})
                    setattr(self.server, "last_request_success", False)
                    return
                if len(body) != length:
                    self._send_json(400, {"success": False, "error": "incomplete_body"})
                    setattr(self.server, "last_request_success", False)
                    return
                if not _valid_github_signature(secret, body, self.headers.get("X-Hub-Signature-256")):
                    self._send_json(401, {"success": False, "error": "invalid_signature"})
                    setattr(self.server, "last_request_success", False)
                    return
                event = (self.headers.get("X-GitHub-Event") or "").strip()
                if not event:
                    self._send_json(400, {"success": False, "error": "missing_github_event"})
                    setattr(self.server, "last_request_success", False)
                    return
                delivery = self.headers.get("X-GitHub-Delivery")
                ignored_summary = _prequeue_ignored_webhook_summary(body=body, event=event, delivery=delivery, state=state)
                spool_path = _webhook_delivery_spool_path(delivery, body)
                spool_payload = {
                    "schema_version": 1,
                    "status": "accepted",
                    "accepted_at": datetime.now(timezone.utc).isoformat(),
                    "event": event,
                    "delivery": delivery,
                    "body": body.decode("utf-8", errors="replace"),
                }
                if ignored_summary is not None:
                    completed = {
                        **spool_payload,
                        "status": "processed",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "rc": 0,
                        "result": ignored_summary,
                        "prequeue_ignored": True,
                    }
                    try:
                        created = _create_webhook_delivery_spool(spool_path, completed)
                    except Exception as exc:
                        self._send_json(500, {"success": False, "error": "delivery_spool_write_failed", "detail": str(exc), "delivery": delivery})
                        setattr(self.server, "last_request_success", False)
                        return
                    if not created:
                        try:
                            existing = _read_json_file(spool_path, default={})
                        except Exception as exc:
                            self._send_json(500, {"success": False, "error": "delivery_spool_read_failed", "detail": str(exc), "delivery": delivery})
                            setattr(self.server, "last_request_success", False)
                            return
                        duplicate = {
                            "success": True,
                            "action": "duplicate",
                            "event": event,
                            "delivery": delivery,
                            "status": existing.get("status") if isinstance(existing, dict) else None,
                            "spool": str(spool_path),
                        }
                        self._send_json(200 if once else 202, duplicate)
                        setattr(self.server, "last_request_success", True)
                        return
                    status_code = 200 if once else 202
                    response_payload = ignored_summary if once else {"success": True, "action": "ignored", "reason": ignored_summary["result"].get("reason"), "event": event, "delivery": delivery, "spool": str(spool_path)}
                    self._send_json(status_code, response_payload)
                    setattr(self.server, "last_request_success", True)
                    return
                slot_acquired = False
                if not once:
                    if not processing_slot.acquire(blocking=False):
                        self._send_json(503, {"success": False, "error": "review_queue_busy", "delivery": delivery})
                        setattr(self.server, "last_request_success", False)
                        return
                    slot_acquired = True
                try:
                    created = _create_webhook_delivery_spool(spool_path, spool_payload)
                except Exception as exc:
                    if slot_acquired:
                        processing_slot.release()
                    self._send_json(500, {"success": False, "error": "delivery_spool_write_failed", "detail": str(exc), "delivery": delivery})
                    setattr(self.server, "last_request_success", False)
                    return
                if not created:
                    try:
                        existing = _read_json_file(spool_path, default={})
                    except Exception as exc:
                        if slot_acquired:
                            processing_slot.release()
                        self._send_json(500, {"success": False, "error": "delivery_spool_read_failed", "detail": str(exc), "delivery": delivery})
                        setattr(self.server, "last_request_success", False)
                        return
                    existing_status = existing.get("status") if isinstance(existing, dict) else None
                    if existing_status != "accepted":
                        if slot_acquired:
                            processing_slot.release()
                        duplicate = {
                            "success": True,
                            "action": "duplicate",
                            "event": event,
                            "delivery": delivery,
                            "status": existing_status,
                            "spool": str(spool_path),
                        }
                        self._send_json(200 if once else 202, duplicate)
                        setattr(self.server, "last_request_success", True)
                        return
                delivery_force = force if created else False
                def process_delivery() -> None:
                    rc = 1
                    result: Dict[str, Any] = {"success": False, "error": "delivery_not_processed", "event": event, "delivery": delivery}
                    processing_handle = None
                    try:
                        retry_delay = 0.1
                        current: Any = {}
                        while not recovery_stop.is_set():
                            retry_error = "delivery_processing_lock_contended"
                            try:
                                processing_handle = _acquire_webhook_processing_lock(blocking=False)
                                if processing_handle is not None:
                                    current = _read_json_file(spool_path, default={})
                                    break
                            except Exception as exc:
                                retry_error = str(exc)
                            if processing_handle is not None:
                                try:
                                    _release_advisory_lock(processing_handle)
                                finally:
                                    processing_handle = None
                            print(
                                json.dumps(
                                    {
                                        "action": "processing_retry",
                                        "event": event,
                                        "delivery": delivery,
                                        "spool": str(spool_path),
                                        "error": retry_error,
                                        "next_retry_seconds": retry_delay,
                                    },
                                    sort_keys=True,
                                ),
                                file=sys.stderr,
                                flush=True,
                            )
                            if recovery_stop.wait(retry_delay):
                                return
                            retry_delay = min(retry_delay * 2, 30.0)
                        if processing_handle is None:
                            return
                        if not isinstance(current, dict) or current.get("status") != "accepted":
                            print(
                                json.dumps(
                                    {
                                        "action": "already_processed",
                                        "event": event,
                                        "delivery": delivery,
                                        "spool": str(spool_path),
                                        "status": current.get("status") if isinstance(current, dict) else None,
                                    },
                                    sort_keys=True,
                                    default=str,
                                ),
                                file=sys.stderr,
                                flush=True,
                            )
                            return
                        current_body = current.get("body")
                        current_event = str(current.get("event") or "").strip()
                        current_delivery = str(current.get("delivery") or "").strip() or None
                        if not isinstance(current_body, str):
                            result = {"success": False, "error": "accepted delivery body must be a string", "event": current_event or None, "delivery": current_delivery}
                        elif not current_event:
                            result = {"success": False, "error": "accepted delivery event is missing", "event": None, "delivery": current_delivery}
                        else:
                            try:
                                with processing_lock:
                                    rc, result = _run_webhook_event_from_http(
                                        review_runner=review_runner,
                                        body=current_body.encode("utf-8"),
                                        event=current_event,
                                        delivery=current_delivery,
                                        config=config,
                                        state=state,
                                        force=delivery_force,
                                        no_llm=no_llm,
                                        ctx=ctx,
                                    )
                            except Exception as exc:
                                result = {"success": False, "error": str(exc), "event": current_event, "delivery": current_delivery}
                        completed = {**current, "status": "processed" if rc == 0 else "failed", "processed_at": datetime.now(timezone.utc).isoformat(), "rc": rc, "result": result}
                        try:
                            _write_webhook_delivery_spool(spool_path, completed)
                        except Exception as exc:
                            result = {**result, "spool_error": str(exc)}
                        log_payload = {"action": "processed", "event": current_event or None, "delivery": current_delivery, "spool": str(spool_path), "rc": rc, "result": result}
                        print(json.dumps(log_payload, sort_keys=True, default=str), file=sys.stderr, flush=True)
                    finally:
                        if processing_handle is not None:
                            try:
                                _release_advisory_lock(processing_handle)
                            except Exception as exc:
                                print(
                                    json.dumps({"action": "processing_unlock_error", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                                    file=sys.stderr,
                                    flush=True,
                                )
                        processing_slot.release()

                if once:
                    once_handle = None
                    try:
                        once_handle = _acquire_webhook_processing_lock(blocking=True)
                        if once_handle is None:
                            raise RuntimeError("delivery processing lock unavailable")
                        admitted_payload = _read_json_file(spool_path, default={})
                        if not isinstance(admitted_payload, dict) or admitted_payload.get("status") != "accepted":
                            duplicate = {
                                "success": True,
                                "action": "duplicate",
                                "event": event,
                                "delivery": delivery,
                                "status": admitted_payload.get("status") if isinstance(admitted_payload, dict) else None,
                                "spool": str(spool_path),
                            }
                            self._send_json(200, duplicate)
                            setattr(self.server, "last_request_success", True)
                            return
                        rc = 1
                        once_body = admitted_payload.get("body")
                        once_event = str(admitted_payload.get("event") or "").strip()
                        once_delivery = str(admitted_payload.get("delivery") or "").strip() or None
                        if not isinstance(once_body, str):
                            result = {"success": False, "error": "accepted delivery body must be a string", "event": once_event or None, "delivery": once_delivery}
                        elif not once_event:
                            result = {"success": False, "error": "accepted delivery event is missing", "event": None, "delivery": once_delivery}
                        else:
                            try:
                                with processing_lock:
                                    rc, result = _run_webhook_event_from_http(
                                        review_runner=review_runner,
                                        body=once_body.encode("utf-8"),
                                        event=once_event,
                                        delivery=once_delivery,
                                        config=config,
                                        state=state,
                                        force=delivery_force,
                                        no_llm=no_llm,
                                        ctx=ctx,
                                    )
                            except Exception as exc:
                                result = {"success": False, "error": str(exc), "event": once_event, "delivery": once_delivery}
                        completed = {**admitted_payload, "status": "processed" if rc == 0 else "failed", "processed_at": datetime.now(timezone.utc).isoformat(), "rc": rc, "result": result}
                        _write_webhook_delivery_spool(spool_path, completed)
                        self._send_json(200 if rc == 0 else 500, result)
                        setattr(self.server, "last_request_success", rc == 0)
                        return
                    except Exception as exc:
                        self._send_json(500, {"success": False, "error": "delivery_processing_failed", "detail": str(exc), "delivery": delivery})
                        setattr(self.server, "last_request_success", False)
                        return
                    finally:
                        if once_handle is not None:
                            try:
                                _release_advisory_lock(once_handle)
                            except Exception as exc:
                                print(
                                    json.dumps({"action": "processing_unlock_error", "spool": str(spool_path), "error": str(exc)}, sort_keys=True),
                                    file=sys.stderr,
                                    flush=True,
                                )

                worker = threading.Thread(target=process_delivery, name=f"pr-review-webhook-{delivery or 'delivery'}")
                worker.start()
                self._send_json(202, {"success": True, "action": "accepted", "event": event, "delivery": delivery, "spool": str(spool_path)})
                setattr(self.server, "last_request_success", True)

        # Keep request handling single-threaded. Accepted deliveries are processed
        # on a bounded background worker, which avoids unbounded handler threads
        # when this localhost listener is exposed through a public tunnel.
        httpd = HTTPServer((host, port), GitHubWebhookHandler)
        recovery_candidates: List[Path] = []
        recovery_snapshot_error: str | None = None
        if not once:
            try:
                spool_dir = _webhook_delivery_spool_dir()
                recovery_candidates = sorted(spool_dir.glob("*.json")) if spool_dir.exists() else []
            except Exception as exc:
                recovery_snapshot_error = str(exc)
        recovery = {"scheduled": not once, "candidates": len(recovery_candidates)}
        recovery_stop = threading.Event()
        recovery_worker: threading.Thread | None = None

        if not once:
            def recover_deliveries() -> None:
                if recovery_snapshot_error is not None:
                    summary: Dict[str, Any] = {"error": recovery_snapshot_error, "attempts": 0}
                else:
                    summary = {
                        "accepted": 0,
                        "recovered": 0,
                        "failed": 0,
                        "locked": 0,
                        "lock_errors": 0,
                        "retryable": 0,
                        "unreadable": 0,
                        "attempts": 0,
                    }
                    try:
                        retry_delay = 0.1
                        attempt = 0
                        while True:
                            if recovery_stop.is_set():
                                summary["stopped"] = True
                                break
                            attempt += 1
                            attempt_summary = _recover_accepted_webhook_deliveries(
                                review_runner=review_runner,
                                config=config,
                                state=state,
                                no_llm=no_llm,
                                processing_lock=processing_lock,
                                processing_slot=processing_slot,
                                spool_paths=recovery_candidates,
                                should_stop=recovery_stop.is_set,
                                ctx=ctx,
                            )
                            if attempt == 1:
                                summary.update(attempt_summary)
                            else:
                                summary["recovered"] += attempt_summary["recovered"]
                                summary["failed"] += attempt_summary["failed"]
                                summary["locked"] = attempt_summary["locked"]
                                summary["lock_errors"] = attempt_summary["lock_errors"]
                                summary["retryable"] = attempt_summary["retryable"]
                                summary["unreadable"] = max(summary["unreadable"], attempt_summary["unreadable"])
                            summary["attempts"] = attempt
                            if not attempt_summary["locked"] and not attempt_summary["lock_errors"] and not attempt_summary["retryable"]:
                                break
                            setattr(httpd, "recovery_summary", {**summary, "retrying": True, "next_retry_seconds": retry_delay})
                            print(
                                json.dumps(
                                    {
                                        "action": "recovery_retry",
                                        "attempts": attempt,
                                        "locked": attempt_summary["locked"],
                                        "lock_errors": attempt_summary["lock_errors"],
                                        "retryable": attempt_summary["retryable"],
                                        "next_retry_seconds": retry_delay,
                                    },
                                    sort_keys=True,
                                ),
                                file=sys.stderr,
                                flush=True,
                            )
                            if _wait_for_webhook_recovery_retry(recovery_stop, retry_delay):
                                summary["stopped"] = True
                                break
                            retry_delay = min(retry_delay * 2, 30.0)
                    except Exception as exc:
                        summary["error"] = str(exc)
                setattr(httpd, "recovery_summary", summary)
                print(
                    json.dumps({"action": "recovery_complete", **summary}, sort_keys=True, default=str),
                    file=sys.stderr,
                    flush=True,
                )

            recovery_worker = threading.Thread(
                target=recover_deliveries,
                name="pr-review-webhook-recovery",
            )
            recovery_worker.start()

        startup = {
            "success": True,
            "action": "listening",
            "host": host,
            "port": port,
            "path": path,
            "once": once,
            "read_timeout": read_timeout,
            "recovery": recovery,
        }
        if getattr(args, "json", False):
            print(json.dumps(startup, sort_keys=True), flush=True)
        else:
            print(f"Hermes PR review webhook receiver listening on http://{host}:{port}{path}", flush=True)
        try:
            if once:
                while not hasattr(httpd, "last_request_success"):
                    httpd.handle_request()
                return 0 if getattr(httpd, "last_request_success", False) else 1
            else:
                httpd.serve_forever()
        finally:
            recovery_stop.set()
            if recovery_worker is not None and recovery_worker.is_alive():
                recovery_worker.join()
            httpd.server_close()
        return 0
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"hermes pr-review webhook-serve: {exc}", file=sys.stderr)
        return 1


def cmd_enable(args: argparse.Namespace) -> int:
    try:
        repo = _normalize_repo_name(getattr(args, "repo", ""))
        config_value = getattr(args, "config", None)
        config_path = Path(config_value).expanduser().resolve() if config_value else _default_watch_config_path()
        raw = _read_json_file(config_path, default={"repos": {}})
        if not isinstance(raw, dict):
            raise ValueError("watch config must be a JSON object")
        repos = raw.get("repos") or {}
        if not isinstance(repos, dict):
            raise ValueError("watch config `repos` must be an object")
        repo_key = _canonical_repo_key(repos, repo)
        local_repo_value = getattr(args, "local_repo", None)
        entry = dict(repos.get(repo_key) or {})
        existing_local_repo = str(entry.get("localRepo") or entry.get("local_repo") or "").strip() or None
        if local_repo_value:
            local_repo = str(Path(local_repo_value).expanduser().resolve())
        elif existing_local_repo:
            local_repo = existing_local_repo
        else:
            local_repo = str(Path.cwd().resolve())
        if local_repo and not Path(local_repo).exists():
            raise ValueError(f"local repo path does not exist: {local_repo}")
        post_comment = getattr(args, "post_comment", None)
        if post_comment is None:
            post_comment = _normalize_bool(entry.get("postComment", entry.get("post_comment")), default=False)
        post_findings_only = getattr(args, "post_findings_only", None)
        if post_findings_only is None:
            post_findings_only = _normalize_bool(entry.get("postFindingsOnly", entry.get("post_findings_only")), default=True)
        review_drafts = getattr(args, "review_drafts", None)
        if review_drafts is None:
            review_drafts = _normalize_bool(entry.get("reviewDrafts", entry.get("review_drafts")), default=False)
        graph_mode = getattr(args, "graph_context", None)
        if graph_mode is None:
            graph_mode = _normalize_graph_mode(entry.get("graphContext", entry.get("graph_context")) or "auto")
        graph_context_binary_value = getattr(args, "graph_context_binary", None)
        clear_graph_context_binary = bool(getattr(args, "clear_graph_context_binary", False))
        if clear_graph_context_binary and graph_context_binary_value is not None:
            raise ValueError("--graph-context-binary and --clear-graph-context-binary cannot be used together")
        if clear_graph_context_binary:
            graph_context_binary = None
        elif graph_context_binary_value is None:
            graph_context_binary = str(entry.get("graphContextBinary") or entry.get("graph_context_binary") or "").strip() or None
        else:
            graph_context_binary = graph_context.resolve_codegraph_binary(str(graph_context_binary_value))
        mode = str(getattr(args, "mode", None) or entry.get("mode") or "balanced").strip() or "balanced"
        max_diff_chars = getattr(args, "max_diff_chars", None)
        if max_diff_chars is None:
            max_diff_chars = entry.get("maxDiffChars", entry.get("max_diff_chars")) or 120_000
        for legacy_key in ("post_comment", "post_findings_only", "review_drafts", "graph_context", "graph_context_binary", "local_repo", "max_diff_chars"):
            entry.pop(legacy_key, None)
        entry.update(
            {
                "enabled": True,
                "postComment": bool(post_comment),
                "postFindingsOnly": bool(post_findings_only),
                "reviewDrafts": bool(review_drafts),
                "graphContext": str(graph_mode),
                "mode": mode,
                "maxDiffChars": max(1_000, int(max_diff_chars)),
            }
        )
        if local_repo:
            entry["localRepo"] = local_repo
        if graph_context_binary:
            entry["graphContextBinary"] = graph_context_binary
        elif clear_graph_context_binary:
            entry.pop("graphContextBinary", None)
        repos[repo_key] = entry
        secret_path, secret, secret_created = _ensure_webhook_secret(getattr(args, "secret_file", None))
        raw["repos"] = repos
        _write_json_file(config_path, raw)
        webhook_url = str(getattr(args, "webhook_url", "") or "").strip() or None
        serve_command = f"hermes pr-review webhook-serve --host 127.0.0.1 --port 8787 --secret-file {shlex.quote(str(secret_path))}"
        graph_binary_option = f" --graph-context-binary {shlex.quote(graph_context_binary)}" if graph_context_binary else ""
        graph_setup_command = f"hermes pr-review graph-setup --local-repo {shlex.quote(str(local_repo))}{graph_binary_option} --install-missing" if local_repo else None
        summary = {
            "success": True,
            "repo": repo_key,
            "config": str(config_path),
            "entry": entry,
            "secret_file": str(secret_path),
            "secret_created": secret_created,
            "webhook_url": webhook_url,
            "github": {
                "payload_url": webhook_url or "https://<your-funnel-host>/webhooks/github",
                "content_type": "application/json",
                "secret_file": str(secret_path),
                "secret_instruction": f"paste the contents of {secret_path}",
                "events": ["pull_request"],
                "ssl_verification": True,
            },
            "commands": {
                "serve": serve_command,
                "graph_setup": graph_setup_command,
                "tailscale_funnel": "hermes pr-review funnel setup",
            },
        }
        if bool(getattr(args, "print_secret", False)):
            summary["secret"] = secret
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"hermes pr-review enable: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Hermes PR review repo enabled")
        print("-----------------------------")
        print(f"repo        : {summary['repo']}")
        print(f"config      : {summary['config']}")
        print(f"local repo  : {entry.get('localRepo') or '(not set)'}")
        posting_label = "disabled (no-post)"
        if entry.get("postComment"):
            posting_label = "enabled (findings-only)" if entry.get("postFindingsOnly") else "enabled"
        print(f"posting     : {posting_label}")
        print(f"graph       : {entry.get('graphContext')}")
        if entry.get("graphContextBinary"):
            print(f"graph binary: {entry.get('graphContextBinary')}")
        if entry.get("graphContext") == "auto" and graph_setup_command:
            print(f"graph setup : {graph_setup_command}")
        print(f"secret file : {summary['secret_file']}" + (" (created)" if secret_created else ""))
        if webhook_url:
            print(f"payload URL : {webhook_url}")
        print("\nGitHub webhook settings:")
        print(f"  Payload URL      : {summary['github']['payload_url']}")
        print("  Content type     : application/json")
        print(f"  Secret           : paste the contents of {summary['secret_file']}")
        print("  Events           : Pull requests only")
        print("  SSL verification : enabled")
        print("\nLocal receiver:")
        print(f"  {serve_command}")
        print("  tailscale funnel --bg 8787")
        if not webhook_url:
            print("\nTip: pass --webhook-url once your Tailscale Funnel hostname is known.")
    return 0
