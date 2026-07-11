import argparse
import json
import stat
import subprocess
from pathlib import Path


import pytest

from plugins.pr_review import cli, onboarding


def _args(**values):
    defaults = {"json": True}
    defaults.update(values)
    return argparse.Namespace(**defaults)


def test_ensure_secret_creates_private_file_and_reuses_without_rotating(tmp_path: Path):
    path = tmp_path / "secret"

    resolved, created = onboarding._ensure_secret(str(path))
    first = path.read_text(encoding="utf-8")
    resolved_again, created_again = onboarding._ensure_secret(str(path))

    assert resolved == resolved_again == path.resolve()
    assert created is True
    assert created_again is False
    assert path.read_text(encoding="utf-8") == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(first.strip()) >= 32


def test_ensure_secret_rejects_symlink(tmp_path: Path):
    target = tmp_path / "target"
    target.write_text("x" * 40, encoding="utf-8")
    link = tmp_path / "secret"
    link.symlink_to(target)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        onboarding._ensure_secret(str(link))


def test_render_service_unit_uses_managed_marker_and_never_secret_contents(tmp_path: Path):
    hermes = tmp_path / "hermes%binary"
    secret = tmp_path / "secret file"

    unit = onboarding.render_service_unit(hermes_binary=hermes, secret_file=secret, host="127.0.0.1", port=8787)

    assert unit.startswith(onboarding.SERVICE_MARKER)
    assert "NoNewPrivileges=true" in unit
    assert "PrivateTmp=true" in unit
    assert 'Environment="HERMES_HOME=' in unit
    assert "super-secret" not in unit
    assert str(secret) in unit
    assert "hermes%%binary" in unit
    assert "--secret-file" in unit


@pytest.mark.parametrize("host,port", [("0.0.0.0", 8787), ("example.com", 8787), ("127.0.0.1", 0), ("127.0.0.1", 65536)])
def test_validate_receiver_bind_rejects_public_hosts_and_invalid_ports(host: str, port: int):
    with pytest.raises(RuntimeError):
        onboarding._validate_receiver_bind(host, port)


def test_service_install_refuses_unmanaged_unit(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    unit = tmp_path / "receiver.service"
    unit.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")

    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=True, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "refusing to overwrite unmanaged unit" in payload["error"]
    assert unit.read_text(encoding="utf-8").startswith("[Service]")


def test_service_install_refuses_symlinked_unit(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    target = tmp_path / "target.service"
    target.write_text("do not replace\n", encoding="utf-8")
    unit = tmp_path / "receiver.service"
    unit.symlink_to(target)
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")

    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=True, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "symlinked service unit" in payload["error"]
    assert target.read_text(encoding="utf-8") == "do not replace\n"


def test_service_install_writes_managed_unit_and_enables(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    unit = tmp_path / "receiver.service"
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    commands = []
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: commands.append(command) or "")
    monkeypatch.setattr(onboarding, "_wait_for_receiver_health", lambda url: {"status": "ok", "http_status": 200})

    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=False, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["success"] is True
    assert onboarding.SERVICE_MARKER in unit.read_text(encoding="utf-8")
    assert commands == [
        ["/usr/bin/systemctl", "--user", "daemon-reload"],
        ["/usr/bin/systemctl", "--user", "enable", onboarding.SERVICE_NAME],
        ["/usr/bin/systemctl", "--user", "restart", onboarding.SERVICE_NAME],
        ["/usr/bin/systemctl", "--user", "is-active", onboarding.SERVICE_NAME],
    ]


def test_service_install_removes_and_disables_new_unit_when_health_fails(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    unit = tmp_path / "receiver.service"
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    rollback = []
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: "")
    monkeypatch.setattr(onboarding, "_wait_for_receiver_health", lambda url: (_ for _ in ()).throw(RuntimeError("health failed")))
    monkeypatch.setattr(
        onboarding,
        "_run",
        lambda command, **kwargs: rollback.append(command) or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=False, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "health failed" in payload["error"]
    assert not unit.exists()
    assert rollback[0] == ["/usr/bin/systemctl", "--user", "disable", "--now", onboarding.SERVICE_NAME]


def test_service_install_preserves_new_unit_when_rollback_stop_fails(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    unit = tmp_path / "receiver.service"
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: "")
    monkeypatch.setattr(onboarding, "_wait_for_receiver_health", lambda url: (_ for _ in ()).throw(RuntimeError("health failed")))

    def fake_run(command, **kwargs):
        failed = "disable" in command
        return subprocess.CompletedProcess(command, 1 if failed else 0, stdout="", stderr="permission denied" if failed else "")

    monkeypatch.setattr(onboarding, "_run", fake_run)
    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=False, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "rollback failed" in payload["error"]
    assert "unit preserved" in payload["error"]
    assert unit.exists()


def test_service_install_restores_previous_unit_when_restart_fails(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_validate_receiver_bind", lambda host, port: None)
    unit = tmp_path / "receiver.service"
    previous = f"{onboarding.SERVICE_MARKER}\n[Service]\nExecStart=/old/hermes\n"
    unit.write_text(previous, encoding="utf-8")
    hermes = tmp_path / "hermes"
    hermes.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes.chmod(0o755)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")

    def fake_run_checked(command, **kwargs):
        if "restart" in command:
            raise RuntimeError("restart failed")
        return ""

    reloads = []
    monkeypatch.setattr(onboarding, "_run_checked", fake_run_checked)
    def fake_run(command, **kwargs):
        reloads.append(command)
        inactive = "is-enabled" in command or "is-active" in command
        return subprocess.CompletedProcess(command, 1 if inactive else 0, stdout="", stderr="")

    monkeypatch.setattr(onboarding, "_run", fake_run)

    rc = onboarding.cmd_service_install(
        _args(unit_file=str(unit), hermes_binary=str(hermes), secret_file=str(tmp_path / "secret"), host="127.0.0.1", port=8787, no_start=False, force=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "restart failed" in payload["error"]
    assert unit.read_text(encoding="utf-8") == previous
    assert reloads == [
        ["/usr/bin/systemctl", "--user", "is-enabled", onboarding.SERVICE_NAME],
        ["/usr/bin/systemctl", "--user", "is-active", onboarding.SERVICE_NAME],
        ["/usr/bin/systemctl", "--user", "daemon-reload"],
        ["/usr/bin/systemctl", "--user", "disable", onboarding.SERVICE_NAME],
        ["/usr/bin/systemctl", "--user", "stop", onboarding.SERVICE_NAME],
    ]


def test_service_restart_waits_for_health(monkeypatch, tmp_path: Path, capsys):
    commands = []
    unit = tmp_path / "receiver.service"
    unit.write_text(f"{onboarding.SERVICE_MARKER}\n[Service]\n", encoding="utf-8")
    statuses = iter([{"success": False}, {"success": True, "health": {"status": "ok"}}])
    monkeypatch.setattr(onboarding, "default_unit_path", lambda: unit)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: commands.append(command) or "")
    monkeypatch.setattr(onboarding, "collect_service_status", lambda **kwargs: next(statuses))
    monkeypatch.setattr(onboarding.time, "sleep", lambda delay: None)

    rc = onboarding.cmd_service_restart(_args(receiver_url="http://127.0.0.1:8787/healthz"))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["success"] is True
    assert commands == [["/usr/bin/systemctl", "--user", "restart", onboarding.SERVICE_NAME]]


def test_service_remove_requires_apply(capsys):
    rc = onboarding.cmd_service_remove(_args(apply=False, unit_file=None, force=False))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "without --apply" in payload["error"]


def test_service_remove_rejects_symlink_and_preserves_target(monkeypatch, tmp_path: Path, capsys):
    target = tmp_path / "target.service"
    target.write_text(f"{onboarding.SERVICE_MARKER}\n[Service]\n", encoding="utf-8")
    unit = tmp_path / "receiver.service"
    unit.symlink_to(target)
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")

    rc = onboarding.cmd_service_remove(_args(apply=True, unit_file=str(unit), force=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "symlinked service unit" in payload["error"]
    assert unit.is_symlink()
    assert target.read_text(encoding="utf-8").startswith(onboarding.SERVICE_MARKER)


def test_service_remove_preserves_unit_when_stop_fails(monkeypatch, tmp_path: Path, capsys):
    unit = tmp_path / "receiver.service"
    unit.write_text(f"{onboarding.SERVICE_MARKER}\n[Service]\n", encoding="utf-8")
    monkeypatch.setattr(onboarding, "_require_linux_systemd", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(
        onboarding,
        "_run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="permission denied"),
    )

    rc = onboarding.cmd_service_remove(_args(apply=True, unit_file=str(unit), force=False))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "unit preserved" in payload["error"]
    assert unit.exists()


def test_collect_funnel_status_extracts_public_urls(monkeypatch):
    raw = {
        "Web": {"bird.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}}},
        "AllowFunnel": {"bird.tailnet.ts.net:443": True},
    }
    monkeypatch.setattr(onboarding, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: json.dumps(raw))

    payload = onboarding.collect_funnel_status()

    assert payload["success"] is True
    assert payload["webhook_url"] == "https://bird.tailnet.ts.net/webhooks/github"
    assert payload["health_url"] == "https://bird.tailnet.ts.net/healthz"


def test_collect_funnel_status_ignores_unrelated_proxy(monkeypatch):
    raw = {
        "Web": {"bird.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}},
        "AllowFunnel": {"bird.tailnet.ts.net:443": True},
    }
    monkeypatch.setattr(onboarding, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: json.dumps(raw))

    payload = onboarding.collect_funnel_status(port=8787)

    assert payload["success"] is False
    assert payload["active"] is False
    assert payload["webhook_url"] is None


def test_funnel_setup_uses_noninteractive_background_mode(monkeypatch, capsys):
    commands = []
    monkeypatch.setattr(onboarding, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: commands.append(command) or "")
    monkeypatch.setattr(
        onboarding,
        "collect_funnel_status",
        lambda *, port: {
            "success": True,
            "active": True,
            "port": port,
            "health_url": "https://bird.example/healthz",
            "webhook_url": "https://bird.example/webhooks/github",
        },
    )
    monkeypatch.setattr(onboarding, "_probe_url", lambda url, timeout=3.0, expected_service=None: {"status": "ok", "http_status": 200})

    rc = onboarding.cmd_funnel_setup(_args(port=8787, timeout=2.0, apply=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["success"] is True
    assert payload["applied"] is True
    assert commands == [["/usr/bin/tailscale", "funnel", "--bg", "--yes", "8787"]]


def test_funnel_setup_plan_and_unhealthy_receiver_never_mutate(monkeypatch, capsys):
    commands = []
    monkeypatch.setattr(onboarding, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: commands.append(command) or "")
    monkeypatch.setattr(
        onboarding,
        "collect_funnel_status",
        lambda *, port: {"success": False, "active": False, "raw": {}, "hostname": None, "base_url": None, "webhook_url": None},
    )
    monkeypatch.setattr(onboarding, "_probe_url", lambda *args, **kwargs: {"status": "ok", "http_status": 200})

    plan_rc = onboarding.cmd_funnel_setup(_args(port=8787, timeout=2.0, apply=False))
    plan = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(onboarding, "_probe_url", lambda *args, **kwargs: {"status": "fail", "reason": "connection refused"})
    unhealthy_rc = onboarding.cmd_funnel_setup(_args(port=8787, timeout=2.0, apply=True))
    unhealthy = json.loads(capsys.readouterr().out)

    assert plan_rc == 0
    assert plan["applied"] is False
    assert unhealthy_rc == 1
    assert "Funnel was not changed" in unhealthy["error"]
    assert commands == []


def test_funnel_setup_refuses_unrelated_existing_route(monkeypatch, capsys):
    commands = []
    raw = {
        "Web": {"bird.tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}},
        "AllowFunnel": {"bird.tailnet.ts.net:443": True},
    }
    monkeypatch.setattr(onboarding, "_tailscale_binary", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(onboarding, "_probe_url", lambda *args, **kwargs: {"status": "ok", "http_status": 200})
    monkeypatch.setattr(
        onboarding,
        "collect_funnel_status",
        lambda *, port: {"success": False, "active": False, "raw": raw, "hostname": None, "base_url": None, "webhook_url": None},
    )
    monkeypatch.setattr(onboarding, "_run_checked", lambda command, **kwargs: commands.append(command) or "")

    rc = onboarding.cmd_funnel_setup(_args(port=8787, timeout=2.0, apply=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "unrelated existing Funnel configuration" in payload["error"]
    assert commands == []


@pytest.mark.parametrize(
    "value",
    [
        "http://bird.example/webhooks/github",
        "https://user:pass@bird.example/webhooks/github",
        "https://bird.example/wrong",
        "https://bird.example/webhooks/github?secret=nope",
    ],
)
def test_normalize_webhook_url_rejects_unsafe_or_wrong_urls(value: str):
    with pytest.raises(RuntimeError):
        onboarding._normalize_webhook_url(value)


def test_normalize_webhook_url_adds_expected_path():
    assert onboarding._normalize_webhook_url("https://bird.example/") == "https://bird.example/webhooks/github"


def test_repo_disable_requires_apply_and_preserves_entry(monkeypatch, tmp_path: Path, capsys):
    registry = tmp_path / "repos.json"
    registry.write_text(
        json.dumps({"repos": {"owner/repo": {"enabled": True, "localRepo": "/checkout", "webhookUrl": "https://bird.example/webhooks/github"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "default_registry_path", lambda: registry)

    plan_rc = onboarding.cmd_repo_disable(_args(repo="owner/repo", apply=False))
    plan = json.loads(capsys.readouterr().out)
    apply_rc = onboarding.cmd_repo_disable(_args(repo="owner/repo", apply=True))
    applied = json.loads(capsys.readouterr().out)

    saved = json.loads(registry.read_text(encoding="utf-8"))["repos"]["owner/repo"]
    assert plan_rc == 1
    assert "without --apply" in plan["error"]
    assert apply_rc == 0
    assert applied["artifacts_preserved"] is True
    assert saved["enabled"] is False
    assert saved["localRepo"] == "/checkout"
    assert saved["webhookUrl"] == "https://bird.example/webhooks/github"


def test_list_hooks_flattens_all_paginated_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(
        onboarding.core,
        "run_gh",
        lambda args, **kwargs: calls.append(args) or json.dumps([[{"id": 1}], [{"id": 2}], []]),
    )

    hooks = onboarding._list_hooks("owner/repo")

    assert [hook["id"] for hook in hooks] == [1, 2]
    assert calls == [["api", "--paginate", "--slurp", "repos/owner/repo/hooks?per_page=100"]]


def test_webhook_setup_without_apply_is_plan_only(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setattr(onboarding, "_list_hooks", lambda repo: [])
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: None)
    called = []
    secret = tmp_path / "secret"
    monkeypatch.setattr(onboarding, "_gh_api_json", lambda *args, **kwargs: called.append((args, kwargs)))

    rc = onboarding.cmd_webhook_setup(
        _args(repo="owner/repo", url="https://bird.example/webhooks/github", secret_file=str(secret), apply=False)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["action"] == "create"
    assert payload["applied"] is False
    assert called == []
    assert "secret" not in payload
    assert not secret.exists()


def test_webhook_setup_apply_sends_secret_only_in_stdin_payload(monkeypatch, tmp_path: Path, capsys):
    secret = tmp_path / "secret"
    secret.write_text("s" * 48 + "\n", encoding="utf-8")
    secret.chmod(0o600)
    calls = []
    monkeypatch.setattr(onboarding, "_list_hooks", lambda repo: [])
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: None)

    def fake_api(args, payload=None, timeout=60):
        calls.append((args, payload))
        return {
            "id": 123,
            "active": True,
            "events": ["pull_request"],
            "config": {"url": "https://bird.example/webhooks/github", "content_type": "json", "insecure_ssl": "0"},
        }

    monkeypatch.setattr(onboarding, "_gh_api_json", fake_api)
    persisted = []
    monkeypatch.setattr(onboarding, "_persist_hook_metadata", lambda repo, **kwargs: persisted.append((repo, kwargs)))

    rc = onboarding.cmd_webhook_setup(
        _args(repo="owner/repo", url="https://bird.example/webhooks/github", secret_file=str(secret), apply=True)
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert rc == 0
    assert payload["hook_id"] == 123
    assert "s" * 48 not in output
    assert calls[0][0][:3] == ["--method", "POST", "repos/owner/repo/hooks"]
    assert calls[0][1]["config"]["secret"] == "s" * 48
    assert persisted == [("owner/repo", {"url": "https://bird.example/webhooks/github", "hook_id": 123})]


def test_webhook_setup_reports_remote_success_when_metadata_persistence_fails(monkeypatch, tmp_path: Path, capsys):
    secret = tmp_path / "secret"
    secret.write_text("s" * 48 + "\n", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.setattr(onboarding, "_list_hooks", lambda repo: [])
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: None)
    monkeypatch.setattr(
        onboarding,
        "_gh_api_json",
        lambda *args, **kwargs: {"id": 321, "active": True, "events": ["pull_request"], "config": {"url": "https://bird.example/webhooks/github"}},
    )
    monkeypatch.setattr(onboarding, "_persist_hook_metadata", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))

    rc = onboarding.cmd_webhook_setup(
        _args(repo="owner/repo", url="https://bird.example/webhooks/github", secret_file=str(secret), apply=True)
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["remote_mutation_succeeded"] is True
    assert payload["hook_id"] == 321
    assert "--adopt-hook-id 321" in payload["recovery"]


def test_webhook_setup_requires_explicit_adoption_for_matching_unowned_hook(monkeypatch, tmp_path: Path, capsys):
    hook = {
        "id": 77,
        "active": True,
        "events": ["pull_request"],
        "config": {"url": "https://bird.example/webhooks/github", "content_type": "json", "insecure_ssl": "0"},
    }
    monkeypatch.setattr(onboarding, "_list_hooks", lambda repo: [hook])
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: None)

    refused_rc = onboarding.cmd_webhook_setup(
        _args(repo="owner/repo", url="https://bird.example/webhooks/github", secret_file=str(tmp_path / "secret"), apply=False)
    )
    refused = json.loads(capsys.readouterr().out)
    adopt_rc = onboarding.cmd_webhook_setup(
        _args(
            repo="owner/repo",
            url="https://bird.example/webhooks/github",
            secret_file=str(tmp_path / "secret"),
            adopt_hook_id=77,
            apply=False,
        )
    )
    adopt = json.loads(capsys.readouterr().out)

    assert refused_rc == 1
    assert "--adopt-hook-id" in refused["error"]
    assert adopt_rc == 0
    assert adopt["action"] == "adopt"
    assert adopt["hook_id"] == 77


def test_webhook_remove_requires_apply(capsys):
    rc = onboarding.cmd_webhook_remove(_args(repo="owner/repo", hook_id=123, apply=False))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "without --apply" in payload["error"]


def test_webhook_remove_refuses_non_owned_hook_id(monkeypatch, capsys):
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: 123)
    called = []
    monkeypatch.setattr(onboarding, "_gh_api_json", lambda *args, **kwargs: called.append((args, kwargs)))

    rc = onboarding.cmd_webhook_remove(_args(repo="owner/repo", hook_id=456, apply=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "owned hook ID 123" in payload["error"]
    assert called == []


def test_webhook_remove_reports_remote_success_when_metadata_clear_fails(monkeypatch, capsys):
    monkeypatch.setattr(onboarding, "_stored_hook_id", lambda repo: 123)
    calls = []

    def fake_api(args, **kwargs):
        calls.append(args)
        if "DELETE" in args:
            return None
        return {"id": 123, "active": True, "events": ["pull_request"], "config": {"url": "https://bird.example/webhooks/github"}}

    monkeypatch.setattr(onboarding, "_gh_api_json", fake_api)
    monkeypatch.setattr(onboarding, "_persist_hook_metadata", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read only")))

    rc = onboarding.cmd_webhook_remove(_args(repo="owner/repo", hook_id=123, apply=True))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["remote_mutation_succeeded"] is True
    assert payload["stale_local_metadata"] is True
    assert payload["hook_id"] == 123
    assert any("DELETE" in call for call in calls)


def test_public_onboarding_commands_are_registered():
    parser = argparse.ArgumentParser()
    cli.register_cli(parser)

    doctor = parser.parse_args(["doctor", "--json"])
    disable = parser.parse_args(["disable", "owner/repo", "--apply"])
    service = parser.parse_args(["service", "install", "--no-start"])
    restart = parser.parse_args(["service", "restart", "--json"])
    funnel = parser.parse_args(["funnel", "status", "--verify"])
    webhook = parser.parse_args(["webhook", "setup", "owner/repo", "--url", "https://bird.example"])

    assert doctor.pr_review_command == "doctor"
    assert disable.pr_review_command == "disable"
    assert service.service_command == "install"
    assert restart.service_command == "restart"
    assert funnel.funnel_command == "status"
    assert webhook.webhook_command == "setup"
