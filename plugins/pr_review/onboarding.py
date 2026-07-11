from __future__ import annotations

import argparse
import ipaddress
import json
import os
import platform
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Sequence

from . import core, graph_context


SERVICE_NAME = "hermes-pr-review-webhook.service"
SERVICE_MARKER = "# Managed by hermes pr-review service install"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_HEALTH_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/healthz"


def reviewer_root() -> Path:
    return core.artifacts_root().parent


def default_secret_path() -> Path:
    return reviewer_root() / "webhook-secret"


def default_registry_path() -> Path:
    return reviewer_root() / "repos.json"


def default_unit_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")).expanduser()
    return config_home / "systemd" / "user" / SERVICE_NAME


def _local_health_url(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{port}/healthz"


def _validate_receiver_bind(host: str, port: int) -> None:
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise RuntimeError("managed receiver host must be a loopback address (127.0.0.1, ::1, or localhost)")
    if not 1 <= port <= 65535:
        raise RuntimeError("receiver port must be between 1 and 65535")

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = "127.0.0.1" if host == "localhost" else host
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((bind_host, port))
    except OSError as exc:
        health = _probe_url(_local_health_url(host, port), timeout=2.0, expected_service="hermes-pr-review-webhook")
        if health.get("status") != "ok":
            raise RuntimeError(f"receiver port {host}:{port} is already occupied by another process") from exc
    finally:
        probe.close()


def _run(command: Sequence[str], *, input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(part) for part in command],
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"failed to run {command[0]}: {exc}") from exc


def _run_checked(command: Sequence[str], *, input_text: str | None = None, timeout: int = 120) -> str:
    proc = _run(command, input_text=input_text, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise RuntimeError(f"{' '.join(str(part) for part in command[:3])} failed: {detail[:2000]}")
    return proc.stdout


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _write_text_atomic(path: Path, value: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _secret_candidate(path_value: str | None) -> tuple[Path, Path]:
    raw = Path(path_value).expanduser() if path_value else default_secret_path()
    return raw, raw.resolve(strict=False)


def _ensure_secret(path_value: str | None) -> tuple[Path, bool]:
    raw, path = _secret_candidate(path_value)
    if raw.is_symlink():
        raise RuntimeError(f"webhook secret must not be a symlink: {path}")
    if raw.exists():
        if not raw.is_file():
            raise RuntimeError(f"webhook secret is not a regular file: {path}")
        secret = raw.read_text(encoding="utf-8").strip()
        if len(secret) < 32:
            raise RuntimeError(f"webhook secret is too short: {path}")
        raw.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return path, False
    raw.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(raw, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raw.unlink(missing_ok=True)
        raise
    return path, True


def _probe_url(url: str, timeout: float = 3.0, *, expected_service: str | None = None) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            payload = json.loads(body) if body.strip().startswith("{") else None
            service_ok = not expected_service or (isinstance(payload, dict) and payload.get("service") == expected_service)
            ok = response.status == 200 and service_ok and (not isinstance(payload, dict) or payload.get("success") is True)
            return {"status": "ok" if ok else "fail", "http_status": response.status, "body": payload}
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return {"status": "fail", "reason": str(exc)}


def _wait_for_receiver_health(url: str, *, attempts: int = 10, delay: float = 0.2) -> Dict[str, Any]:
    health: Dict[str, Any] = {"status": "fail", "reason": "health check not attempted"}
    for attempt in range(attempts):
        health = _probe_url(url, expected_service="hermes-pr-review-webhook")
        if health.get("status") == "ok":
            return health
        if attempt < attempts - 1:
            time.sleep(delay)
    raise RuntimeError(f"receiver did not become healthy at {url}: {health.get('reason') or health.get('body') or health}")


def _check_command(name: str, *, required: bool, repair: str) -> Dict[str, Any]:
    path = shutil.which(name)
    return {
        "name": name,
        "status": "ok" if path else ("fail" if required else "warn"),
        "required": required,
        "path": path,
        "repair": None if path else repair,
    }


def collect_doctor(*, receiver_url: str = DEFAULT_HEALTH_URL) -> Dict[str, Any]:
    checks: list[Dict[str, Any]] = [
        _check_command("git", required=True, repair="Install Git and rerun `hermes pr-review doctor`."),
        _check_command("gh", required=True, repair="Install GitHub CLI: https://cli.github.com/"),
        _check_command("tailscale", required=False, repair="Install Tailscale or use another HTTPS reverse proxy."),
        _check_command("npm", required=False, repair="Install Node/npm only if you want CodeGraph context."),
    ]
    gh = next(row for row in checks if row["name"] == "gh")
    if gh["status"] == "ok":
        proc = _run([str(gh["path"]), "auth", "status"], timeout=30)
        checks.append(
            {
                "name": "gh_auth",
                "status": "ok" if proc.returncode == 0 else "fail",
                "required": True,
                "repair": None if proc.returncode == 0 else "Run `gh auth login` and grant access to the repositories you review.",
            }
        )

    checks.append(
        {
            "name": "platform",
            "status": "ok" if platform.system() == "Linux" else "warn",
            "required": False,
            "value": platform.system(),
            "repair": None if platform.system() == "Linux" else "Automatic service management currently supports Linux user systemd only; run webhook-serve under your process manager.",
        }
    )
    systemctl = shutil.which("systemctl")
    service_status = "warn"
    service_reason = "systemctl not available"
    if systemctl and platform.system() == "Linux":
        manager = _run([systemctl, "--user", "show-environment"], timeout=15)
        manager_ok = manager.returncode == 0
        checks.append(
            {
                "name": "user_systemd",
                "status": "ok" if manager_ok else "warn",
                "required": False,
                "repair": None if manager_ok else "User systemd is unavailable; run webhook-serve under your process manager.",
            }
        )
        if manager_ok:
            proc = _run([systemctl, "--user", "is-active", SERVICE_NAME], timeout=15)
            service_status = "ok" if proc.returncode == 0 and proc.stdout.strip() == "active" else "warn"
            service_reason = None if service_status == "ok" else "Install the receiver with `hermes pr-review service install`."
    checks.append({"name": "receiver_service", "status": service_status, "required": False, "repair": service_reason})

    secret = default_secret_path()
    secret_ok = secret.is_file() and not secret.is_symlink() and not (secret.stat().st_mode & 0o077)
    checks.append(
        {
            "name": "webhook_secret",
            "status": "ok" if secret_ok else "warn",
            "required": False,
            "path": str(secret),
            "repair": None if secret_ok else "Run `hermes pr-review enable OWNER/REPO` or `hermes pr-review service install`.",
        }
    )

    try:
        registry = _read_json(default_registry_path(), {"repos": {}})
        registry_error = None
    except (OSError, json.JSONDecodeError) as exc:
        registry = {"repos": {}}
        registry_error = str(exc)
    repos = registry.get("repos") if isinstance(registry, dict) else None
    repo_count = len(repos) if isinstance(repos, dict) else 0
    checks.append(
        {
            "name": "repo_registry",
            "status": "fail" if registry_error else ("ok" if repo_count else "warn"),
            "required": bool(registry_error),
            "repos": repo_count,
            "repair": (
                f"Repair malformed registry JSON at {default_registry_path()}: {registry_error}"
                if registry_error
                else (None if repo_count else "Enable a repository with `hermes pr-review enable OWNER/REPO --local-repo /path/to/repo`.")
            ),
        }
    )

    receiver = _probe_url(receiver_url, expected_service="hermes-pr-review-webhook")
    checks.append(
        {
            "name": "receiver_health",
            "status": receiver["status"] if service_status == "ok" else "warn",
            "required": False,
            "url": receiver_url,
            "detail": receiver,
            "repair": None if receiver["status"] == "ok" else "Start the receiver with `hermes pr-review service install` or your process manager.",
        }
    )

    codegraph = None
    try:
        codegraph = graph_context.resolve_codegraph_binary(None)
    except graph_context.GraphContextError:
        pass
    checks.append(
        {
            "name": "codegraph",
            "status": "ok" if codegraph else "warn",
            "required": False,
            "binary": codegraph,
            "repair": None if codegraph else "Optional: run `hermes pr-review graph-setup --local-repo /path/to/repo --install-missing`.",
        }
    )
    failures = [row for row in checks if row.get("status") == "fail"]
    warnings = [row for row in checks if row.get("status") == "warn"]
    next_steps = list(dict.fromkeys(str(row["repair"]) for row in checks if row.get("repair")))
    return {"success": not failures, "checks": checks, "failures": len(failures), "warnings": len(warnings), "next_steps": next_steps}


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = collect_doctor(receiver_url=str(getattr(args, "receiver_url", DEFAULT_HEALTH_URL)))
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Hermes PR review doctor")
        print("-----------------------")
        for row in payload["checks"]:
            print(f"  {row['name']:<18}: {str(row['status']).upper()}")
            if row.get("repair"):
                print(f"    next: {row['repair']}")
        print(f"\nRequired checks: {'ready' if payload['success'] else 'not ready'}; optional warnings: {payload['warnings']}")
    return 0 if payload["success"] else 1


def _resolve_hermes_binary(explicit: str | None) -> Path:
    candidate = explicit or shutil.which("hermes")
    if not candidate and Path(sys.argv[0]).name == "hermes":
        candidate = sys.argv[0]
    if not candidate:
        raise RuntimeError("could not locate the Hermes executable; pass --hermes-binary /absolute/path/to/hermes")
    path = Path(candidate).expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"Hermes executable is not runnable: {path}")
    return path


def _unit_quote(value: str | Path) -> str:
    text = str(value)
    if any(char in text for char in ("\n", "\r", "\x00")):
        raise RuntimeError("systemd unit values must not contain control characters")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def _unit_path(value: str | Path) -> str:
    text = str(value)
    if not text.startswith("/") or any(char in text for char in ("\n", "\r", "\x00")):
        raise RuntimeError("systemd working directory must be an absolute path without control characters")
    return text.replace("\\", "\\x5c").replace(" ", "\\x20").replace("%", "%%")


def render_service_unit(
    *,
    hermes_binary: Path,
    secret_file: Path,
    host: str,
    port: int,
    hermes_home: Path | None = None,
) -> str:
    exec_args = [
        hermes_binary,
        "pr-review",
        "webhook-serve",
        "--host",
        host,
        "--port",
        str(port),
        "--secret-file",
        secret_file,
        "--json",
    ]
    exec_start = " ".join(_unit_quote(part) for part in exec_args)
    profile_home = hermes_home or reviewer_root().parent
    return f"""{SERVICE_MARKER}
[Unit]
Description=Hermes PR Review webhook receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={_unit_path(Path.home())}
Environment={_unit_quote(f"HERMES_HOME={profile_home}")}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""


def _require_linux_systemd() -> str:
    if platform.system() != "Linux":
        raise RuntimeError("automatic receiver service management currently supports Linux user systemd only")
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise RuntimeError("systemctl is not installed; run webhook-serve under your process manager")
    return systemctl


def cmd_service_install(args: argparse.Namespace) -> int:
    try:
        systemctl = _require_linux_systemd()
        hermes_binary = _resolve_hermes_binary(getattr(args, "hermes_binary", None))
        host = str(getattr(args, "host", DEFAULT_HOST))
        port = int(getattr(args, "port", DEFAULT_PORT))
        _validate_receiver_bind(host, port)
        secret_file, secret_created = _ensure_secret(getattr(args, "secret_file", None))
        raw_unit_path = Path(getattr(args, "unit_file", None) or default_unit_path()).expanduser()
        if raw_unit_path.is_symlink():
            raise RuntimeError(f"refusing to replace symlinked service unit {raw_unit_path}")
        unit_path = raw_unit_path.resolve(strict=False)
        rendered = render_service_unit(
            hermes_binary=hermes_binary,
            secret_file=secret_file,
            host=host,
            port=port,
            hermes_home=reviewer_root().parent,
        )
        previous = unit_path.read_bytes() if unit_path.exists() else None
        previous_mode = stat.S_IMODE(unit_path.stat().st_mode) if unit_path.exists() else None
        previous_enabled = False
        previous_active = False
        if previous is not None:
            existing = previous.decode("utf-8")
            if SERVICE_MARKER not in existing and not getattr(args, "force", False):
                raise RuntimeError(f"refusing to overwrite unmanaged unit {unit_path}; pass --force to replace it")
            previous_enabled = _run([systemctl, "--user", "is-enabled", SERVICE_NAME], timeout=30).returncode == 0
            previous_active = _run([systemctl, "--user", "is-active", SERVICE_NAME], timeout=30).returncode == 0
        _write_text_atomic(unit_path, rendered, mode=0o644)
        health = None
        try:
            _run_checked([systemctl, "--user", "daemon-reload"], timeout=30)
            _run_checked([systemctl, "--user", "enable", SERVICE_NAME], timeout=60)
            if not getattr(args, "no_start", False):
                _run_checked([systemctl, "--user", "restart", SERVICE_NAME], timeout=60)
                _run_checked([systemctl, "--user", "is-active", SERVICE_NAME], timeout=30)
                health = _wait_for_receiver_health(_local_health_url(host, port))
        except Exception as install_error:
            rollback_error = None
            if previous is None:
                stop = _run([systemctl, "--user", "disable", "--now", SERVICE_NAME], timeout=60)
                if stop.returncode == 0:
                    unit_path.unlink(missing_ok=True)
                else:
                    detail = (stop.stderr or stop.stdout or f"exit {stop.returncode}").strip()
                    rollback_error = f"failed to stop/disable new service; unit preserved: {detail[:2000]}"
            else:
                _write_text_atomic(unit_path, previous.decode("utf-8"), mode=previous_mode or 0o644)
            reload_result = _run([systemctl, "--user", "daemon-reload"], timeout=30)
            if reload_result.returncode != 0:
                detail = (reload_result.stderr or reload_result.stdout or f"exit {reload_result.returncode}").strip()
                rollback_error = f"{rollback_error + '; ' if rollback_error else ''}daemon-reload failed: {detail[:2000]}"
            if previous is not None:
                restore_commands = [
                    [systemctl, "--user", "enable" if previous_enabled else "disable", SERVICE_NAME],
                    [systemctl, "--user", "restart" if previous_active else "stop", SERVICE_NAME],
                ]
                for command in restore_commands:
                    result = _run(command, timeout=60)
                    if result.returncode != 0:
                        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
                        rollback_error = f"{rollback_error + '; ' if rollback_error else ''}{command[2]} failed: {detail[:2000]}"
            if rollback_error:
                raise RuntimeError(f"{install_error}; rollback failed: {rollback_error}") from install_error
            raise
        payload = {
            "success": True,
            "service": SERVICE_NAME,
            "unit_file": str(unit_path),
            "secret_file": str(secret_file),
            "secret_created": secret_created,
            "started": not getattr(args, "no_start", False),
            "health": health,
            "hermes_binary": str(hermes_binary),
            "hermes_home": str(reviewer_root().parent),
        }
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review receiver service")


def collect_service_status(*, receiver_url: str = DEFAULT_HEALTH_URL) -> Dict[str, Any]:
    systemctl = _require_linux_systemd()
    proc = _run(
        [systemctl, "--user", "show", SERVICE_NAME, "-p", "LoadState", "-p", "UnitFileState", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "NRestarts"],
        timeout=30,
    )
    values: Dict[str, str] = {}
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
    health = _probe_url(receiver_url, expected_service="hermes-pr-review-webhook")
    active = values.get("ActiveState") == "active" and health.get("status") == "ok"
    return {"success": active, "service": SERVICE_NAME, "properties": values, "health": health, "receiver_url": receiver_url}


def cmd_service_status(args: argparse.Namespace) -> int:
    try:
        payload = collect_service_status(receiver_url=str(getattr(args, "receiver_url", DEFAULT_HEALTH_URL)))
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review receiver service")


def cmd_service_restart(args: argparse.Namespace) -> int:
    try:
        systemctl = _require_linux_systemd()
        unit_path = default_unit_path()
        if not unit_path.is_file() or SERVICE_MARKER not in unit_path.read_text(encoding="utf-8"):
            raise RuntimeError(f"refusing to restart unmanaged or missing unit {unit_path}")
        _run_checked([systemctl, "--user", "restart", SERVICE_NAME], timeout=60)
        receiver_url = str(getattr(args, "receiver_url", DEFAULT_HEALTH_URL))
        payload: Dict[str, Any] = {"success": False}
        for attempt in range(10):
            payload = collect_service_status(receiver_url=receiver_url)
            if payload.get("success"):
                break
            if attempt < 9:
                time.sleep(0.2)
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review receiver service restart")


def cmd_service_logs(args: argparse.Namespace) -> int:
    try:
        _require_linux_systemd()
        journalctl = shutil.which("journalctl")
        if not journalctl:
            raise RuntimeError("journalctl is not installed")
        lines = max(1, min(1000, int(getattr(args, "lines", 100))))
        output = _run_checked([journalctl, "--user", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"], timeout=30)
        payload = {"success": True, "service": SERVICE_NAME, "lines": output.splitlines()}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload.get("success"):
        print("\n".join(payload["lines"]))
    else:
        print(f"hermes pr-review service logs: {payload['error']}", file=sys.stderr)
    return 0 if payload.get("success") else 1


def cmd_service_remove(args: argparse.Namespace) -> int:
    try:
        if not getattr(args, "apply", False):
            raise RuntimeError("refusing to remove the receiver service without --apply")
        systemctl = _require_linux_systemd()
        raw_unit_path = Path(getattr(args, "unit_file", None) or default_unit_path()).expanduser()
        if raw_unit_path.is_symlink():
            raise RuntimeError(f"refusing to remove symlinked service unit {raw_unit_path}")
        unit_path = raw_unit_path.resolve(strict=False)
        if unit_path.exists():
            existing = unit_path.read_text(encoding="utf-8")
            if SERVICE_MARKER not in existing and not getattr(args, "force", False):
                raise RuntimeError(f"refusing to remove unmanaged unit {unit_path}; pass --force only if you own it")
        stop = _run([systemctl, "--user", "disable", "--now", SERVICE_NAME], timeout=60)
        if unit_path.exists() and stop.returncode != 0:
            detail = (stop.stderr or stop.stdout or f"exit {stop.returncode}").strip()
            raise RuntimeError(f"failed to stop/disable {SERVICE_NAME}; unit preserved: {detail[:2000]}")
        removed = unit_path.exists()
        unit_path.unlink(missing_ok=True)
        _run_checked([systemctl, "--user", "daemon-reload"], timeout=30)
        payload = {"success": True, "service": SERVICE_NAME, "unit_file": str(unit_path), "removed": removed, "secret_preserved": True}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review receiver service removal")


def _tailscale_binary() -> str:
    binary = shutil.which("tailscale")
    if not binary:
        raise RuntimeError("tailscale is not installed; install it or use another HTTPS reverse proxy")
    return binary


def collect_funnel_status(*, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    tailscale = _tailscale_binary()
    output = _run_checked([tailscale, "funnel", "status", "--json"], timeout=30)
    raw = json.loads(output or "{}")
    web = raw.get("Web") if isinstance(raw, dict) else None
    allowed = raw.get("AllowFunnel") if isinstance(raw, dict) else None
    matching_hosts: list[str] = []
    if isinstance(web, dict):
        for host, settings in web.items():
            handlers = settings.get("Handlers") if isinstance(settings, dict) else None
            if not isinstance(handlers, dict):
                continue
            proxies = [str(handler.get("Proxy") or "") for handler in handlers.values() if isinstance(handler, dict)]
            if any(proxy in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"} for proxy in proxies):
                matching_hosts.append(str(host))
    allowed_hosts = {str(host) for host, enabled in (allowed or {}).items() if enabled} if isinstance(allowed, dict) else set()
    active_hosts = sorted(host for host in matching_hosts if host in allowed_hosts)
    hostname = active_hosts[0].split(":", 1)[0] if active_hosts else None
    base_url = f"https://{hostname}" if hostname else None
    return {
        "success": bool(base_url),
        "active": bool(base_url),
        "hostname": hostname,
        "base_url": base_url,
        "webhook_url": f"{base_url}/webhooks/github" if base_url else None,
        "health_url": f"{base_url}/healthz" if base_url else None,
        "port": port,
        "raw": raw,
    }


def _funnel_conflicts(raw: Any, *, port: int) -> list[str]:
    desired = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    conflicts: list[str] = []
    web = raw.get("Web") if isinstance(raw, dict) else None
    if not isinstance(web, dict):
        return conflicts
    for host, settings in web.items():
        handlers = settings.get("Handlers") if isinstance(settings, dict) else None
        if not isinstance(handlers, dict):
            conflicts.append(str(host))
            continue
        for route, handler in handlers.items():
            proxy = str(handler.get("Proxy") or "") if isinstance(handler, dict) else ""
            if proxy not in desired:
                conflicts.append(f"{host}{route} -> {proxy or 'non-proxy handler'}")
    return conflicts


def cmd_funnel_status(args: argparse.Namespace) -> int:
    try:
        payload = collect_funnel_status(port=int(getattr(args, "port", DEFAULT_PORT)))
        if payload.get("health_url") and getattr(args, "verify", False):
            payload["health"] = _probe_url(
                str(payload["health_url"]),
                timeout=float(getattr(args, "timeout", 5.0)),
                expected_service="hermes-pr-review-webhook",
            )
            payload["success"] = payload["health"].get("status") == "ok"
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review Tailscale Funnel")


def cmd_funnel_setup(args: argparse.Namespace) -> int:
    try:
        tailscale = _tailscale_binary()
        port = int(getattr(args, "port", DEFAULT_PORT))
        local_url = f"http://127.0.0.1:{port}/healthz"
        local_health = _probe_url(local_url, timeout=float(getattr(args, "timeout", 10.0)), expected_service="hermes-pr-review-webhook")
        if local_health.get("status") != "ok":
            raise RuntimeError(f"local receiver is not healthy at {local_url}; Funnel was not changed")
        current = collect_funnel_status(port=port)
        conflicts = _funnel_conflicts(current.get("raw"), port=port)
        if conflicts:
            raise RuntimeError(f"refusing to replace unrelated existing Funnel configuration: {', '.join(conflicts)}")
        if not getattr(args, "apply", False):
            payload = {
                "success": True,
                "applied": False,
                "port": port,
                "local_health": local_health,
                "current": {key: current.get(key) for key in ("active", "hostname", "base_url", "webhook_url")},
                "next_step": "Rerun with --apply to configure Tailscale Funnel for this receiver port.",
            }
        else:
            _run_checked([tailscale, "funnel", "--bg", "--yes", str(port)], timeout=60)
            payload = collect_funnel_status(port=port)
            payload["applied"] = True
            payload["local_health"] = local_health
            payload["health"] = (
                _probe_url(
                    str(payload["health_url"]),
                    timeout=float(getattr(args, "timeout", 10.0)),
                    expected_service="hermes-pr-review-webhook",
                )
                if payload.get("health_url")
                else {"status": "fail", "reason": f"no active Funnel proxy to 127.0.0.1:{port} was reported"}
            )
            payload["success"] = payload.get("active") is True and payload["health"].get("status") == "ok"
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review Tailscale Funnel")


def _normalize_repo(repo: str) -> str:
    value = str(repo or "").strip().strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise RuntimeError("repo must be owner/name")
    return value


def _normalize_webhook_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("webhook URL must be a public HTTPS URL without credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/webhooks/github"
    if path != "/webhooks/github":
        raise RuntimeError("webhook URL path must be /webhooks/github")
    return urllib.parse.urlunparse(("https", parsed.netloc, path, "", "", ""))


def _gh_api_json(args: Sequence[str], *, payload: Any | None = None, timeout: int = 60) -> Any:
    command = ["api", *args]
    input_text = json.dumps(payload) if payload is not None else None
    if payload is not None:
        command.extend(["--input", "-"])
    output = core.run_gh(command, input_text=input_text, timeout=timeout)
    return json.loads(output) if output.strip() else None


def _list_hooks(repo: str) -> list[Dict[str, Any]]:
    output = core.run_gh(["api", "--paginate", "--slurp", f"repos/{repo}/hooks?per_page=100"], timeout=60)
    value = json.loads(output) if output.strip() else []
    pages = value if isinstance(value, list) else []
    hooks: list[Dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            hooks.extend(item for item in page if isinstance(item, dict))
        elif isinstance(page, dict):
            hooks.append(page)
    return hooks


def _public_hook(hook: Dict[str, Any]) -> Dict[str, Any]:
    raw_config = hook.get("config")
    config: Dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    return {
        "id": hook.get("id"),
        "active": hook.get("active"),
        "events": hook.get("events") or [],
        "url": config.get("url"),
        "content_type": config.get("content_type"),
        "ssl_verification": str(config.get("insecure_ssl") or "0") == "0",
    }


def _registry_repo_key(repos: Dict[str, Any], repo: str) -> str:
    return next((str(key) for key in repos if str(key).casefold() == repo.casefold()), repo)


def _persist_hook_metadata(repo: str, *, url: str | None, hook_id: int | None) -> None:
    path = default_registry_path()
    raw = _read_json(path, {"repos": {}})
    if not isinstance(raw, dict):
        raise RuntimeError(f"registry is not a JSON object: {path}")
    repos = raw.setdefault("repos", {})
    if not isinstance(repos, dict):
        raise RuntimeError(f"registry repos is not a JSON object: {path}")
    key = _registry_repo_key(repos, repo)
    entry = dict(repos.get(key) or {})
    if url is None:
        entry.pop("webhookUrl", None)
        entry.pop("githubHookId", None)
    else:
        entry["webhookUrl"] = url
        entry["githubHookId"] = hook_id
    repos[key] = entry
    _write_json(path, raw)


def _stored_hook_id(repo: str) -> int | None:
    raw = _read_json(default_registry_path(), {"repos": {}})
    repos = raw.get("repos") if isinstance(raw, dict) else None
    key = _registry_repo_key(repos, repo) if isinstance(repos, dict) else repo
    entry = repos.get(key) if isinstance(repos, dict) else None
    value = entry.get("githubHookId") if isinstance(entry, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        raise RuntimeError(f"stored GitHub hook ID is invalid for {repo}") from None


def cmd_repo_disable(args: argparse.Namespace) -> int:
    try:
        if not getattr(args, "apply", False):
            raise RuntimeError("refusing to disable the local repository without --apply")
        repo = _normalize_repo(getattr(args, "repo", ""))
        path = default_registry_path()
        raw = _read_json(path, {"repos": {}})
        if not isinstance(raw, dict) or not isinstance(raw.get("repos"), dict):
            raise RuntimeError(f"registry is not a valid repos object: {path}")
        repos = raw["repos"]
        key = _registry_repo_key(repos, repo)
        if key not in repos or not isinstance(repos[key], dict):
            raise RuntimeError(f"repository is not configured: {repo}")
        entry = dict(repos[key])
        entry["enabled"] = False
        repos[key] = entry
        _write_json(path, raw)
        payload = {"success": True, "repo": repo, "enabled": False, "config": str(path), "artifacts_preserved": True}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review repository disabled")


def cmd_webhook_status(args: argparse.Namespace) -> int:
    try:
        repo = _normalize_repo(getattr(args, "repo", ""))
        owned_hook_id = _stored_hook_id(repo)
        hooks = [{**_public_hook(hook), "owned": int(hook.get("id") or 0) == owned_hook_id} for hook in _list_hooks(repo)]
        payload = {"success": True, "repo": repo, "owned_hook_id": owned_hook_id, "hooks": hooks, "count": len(hooks)}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review GitHub webhooks")


def cmd_webhook_setup(args: argparse.Namespace) -> int:
    try:
        repo = _normalize_repo(getattr(args, "repo", ""))
        url = _normalize_webhook_url(getattr(args, "url", ""))
        _, secret_path = _secret_candidate(getattr(args, "secret_file", None))
        hooks = _list_hooks(repo)
        stored_hook_id = _stored_hook_id(repo)
        adopt_hook_id = getattr(args, "adopt_hook_id", None)
        by_id = {int(hook["id"]): hook for hook in hooks if hook.get("id") is not None}
        matching = [hook for hook in hooks if str((hook.get("config") or {}).get("url") or "") == url]

        existing = None
        action = "create"
        if stored_hook_id is not None:
            if adopt_hook_id is not None and int(adopt_hook_id) != stored_hook_id:
                raise RuntimeError(f"repository already owns hook ID {stored_hook_id}; refusing to adopt a different hook")
            existing = by_id.get(stored_hook_id)
            if existing is None:
                raise RuntimeError(f"stored GitHub hook ID {stored_hook_id} no longer exists; inspect webhook status before creating another")
            action = "update"
        elif adopt_hook_id is not None:
            existing = by_id.get(int(adopt_hook_id))
            if existing is None:
                raise RuntimeError(f"requested adoption hook ID does not exist: {adopt_hook_id}")
            existing_url = str((existing.get("config") or {}).get("url") or "")
            if existing_url != url:
                raise RuntimeError(f"hook ID {adopt_hook_id} URL does not match the requested URL")
            action = "adopt"
        elif matching:
            ids = ", ".join(str(hook.get("id")) for hook in matching)
            raise RuntimeError(f"matching webhook exists but is not owned by this setup (ID: {ids}); rerun with --adopt-hook-id ID after inspection")

        plan = {
            "success": True,
            "applied": False,
            "action": action,
            "repo": repo,
            "url": url,
            "hook_id": int(existing["id"]) if existing else None,
            "secret_file": str(secret_path),
        }
        if not getattr(args, "apply", False):
            payload = {**plan, "next_step": "Rerun with --apply to perform the planned GitHub webhook mutation."}
        else:
            secret_path, _ = _ensure_secret(getattr(args, "secret_file", None))
            secret = secret_path.read_text(encoding="utf-8").strip()
            request = {
                "name": "web",
                "active": True,
                "events": ["pull_request"],
                "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"},
            }
            if existing:
                hook = _gh_api_json(["--method", "PATCH", f"repos/{repo}/hooks/{int(existing['id'])}"], payload=request)
            else:
                hook = _gh_api_json(["--method", "POST", f"repos/{repo}/hooks"], payload=request)
            public = _public_hook(hook if isinstance(hook, dict) else {})
            hook_id = int(public["id"])
            try:
                _persist_hook_metadata(repo, url=url, hook_id=hook_id)
            except Exception as persist_error:
                payload = {
                    **plan,
                    "success": False,
                    "remote_mutation_succeeded": True,
                    "action": action,
                    "hook_id": hook_id,
                    "hook": public,
                    "error": f"GitHub webhook {action} succeeded, but local ownership metadata could not be saved: {persist_error}",
                    "recovery": f"Repair {default_registry_path()}, then adopt hook ID {hook_id} with `hermes pr-review webhook setup {repo} --url {url} --adopt-hook-id {hook_id} --apply`.",
                }
                return _print_payload(payload, args, title="Hermes PR review GitHub webhook")
            payload = {**plan, "applied": True, "hook": public, "hook_id": hook_id}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review GitHub webhook")


def cmd_webhook_remove(args: argparse.Namespace) -> int:
    try:
        if not getattr(args, "apply", False):
            raise RuntimeError("refusing to remove a GitHub webhook without --apply")
        repo = _normalize_repo(getattr(args, "repo", ""))
        hook_id = int(getattr(args, "hook_id"))
        stored_hook_id = _stored_hook_id(repo)
        if stored_hook_id is None:
            raise RuntimeError(f"no owned GitHub hook is recorded for {repo}; refusing remote deletion")
        if hook_id != stored_hook_id:
            raise RuntimeError(f"hook ID {hook_id} does not match the owned hook ID {stored_hook_id}")
        hook = _gh_api_json([f"repos/{repo}/hooks/{hook_id}"])
        public = _public_hook(hook if isinstance(hook, dict) else {})
        _gh_api_json(["--method", "DELETE", f"repos/{repo}/hooks/{hook_id}"])
        try:
            _persist_hook_metadata(repo, url=None, hook_id=None)
        except Exception as persist_error:
            payload = {
                "success": False,
                "applied": True,
                "remote_mutation_succeeded": True,
                "repo": repo,
                "hook_id": hook_id,
                "removed": public,
                "secret_preserved": True,
                "stale_local_metadata": True,
                "error": f"GitHub webhook removal succeeded, but local ownership metadata could not be cleared: {persist_error}",
                "recovery": f"Repair {default_registry_path()} and remove `githubHookId`/`webhookUrl` from {repo}.",
            }
            return _print_payload(payload, args, title="Hermes PR review GitHub webhook removal")
        payload = {"success": True, "applied": True, "repo": repo, "removed": public, "secret_preserved": True}
    except Exception as exc:
        payload = {"success": False, "error": str(exc)}
    return _print_payload(payload, args, title="Hermes PR review GitHub webhook removal")


def _print_payload(payload: Dict[str, Any], args: argparse.Namespace, *, title: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload.get("success"):
        print(title)
        print("-" * len(title))
        for key, value in payload.items():
            if key in {"success", "raw", "lines"}:
                continue
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, sort_keys=True)}")
            else:
                print(f"{key}: {value}")
    else:
        print(f"{title}: {payload.get('error') or 'not ready'}", file=sys.stderr)
    return 0 if payload.get("success") else 1
