from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from plugins.pr_review import automation
from plugins.pr_review import cli as pr_review_cli
from plugins.pr_review import core, graph_context


def test_write_json_file_preserves_unrelated_competing_temp_file(tmp_path: Path):
    target = tmp_path / "state.json"
    competing = target.with_suffix(target.suffix + ".tmp")
    competing.write_text("another writer", encoding="utf-8")

    automation._write_json_file(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert (target.stat().st_mode & 0o777) == 0o600
    assert competing.read_text(encoding="utf-8") == "another writer"


def test_write_json_file_cleans_up_unique_temp_after_serialization_failure(tmp_path: Path):
    target = tmp_path / "state.json"

    with pytest.raises(TypeError):
        automation._write_json_file(target, {"not_json": object()})

    assert not target.exists()
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_webhook_delivery_spool_path_contains_traversal_shaped_delivery(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)

    path = automation._webhook_delivery_spool_path("../../outside\\nested", b"payload")

    assert path.parent == spool_dir
    assert path.name == ".._.._outside_nested.json"
    assert path.resolve(strict=False).is_relative_to(spool_dir.resolve(strict=False))


def test_create_webhook_delivery_spool_never_replaces_terminal_delivery(tmp_path: Path):
    spool_path = tmp_path / "delivery-duplicate.json"
    terminal = {"schema_version": 1, "status": "processed", "delivery": "delivery-duplicate", "rc": 0}
    automation._write_webhook_delivery_spool(spool_path, terminal)

    created = automation._create_webhook_delivery_spool(
        spool_path,
        {"schema_version": 1, "status": "accepted", "delivery": "delivery-duplicate"},
    )

    assert created is False
    assert json.loads(spool_path.read_text(encoding="utf-8")) == terminal
    assert list(tmp_path.glob(f".{spool_path.name}.*.admission")) == []


def test_create_webhook_delivery_spool_fsyncs_published_name_before_success(monkeypatch, tmp_path: Path):
    spool_path = tmp_path / "delivery-durable.json"
    events = []
    real_link = os.link

    def tracked_link(source, target):
        events.append("link")
        return real_link(source, target)

    monkeypatch.setattr(os, "link", tracked_link)
    monkeypatch.setattr(automation, "_fsync_directory", lambda path: events.append("fsync"))

    assert automation._create_webhook_delivery_spool(
        spool_path,
        {"schema_version": 1, "status": "accepted", "delivery": "delivery-durable"},
    ) is True

    link_index = events.index("link")
    assert "fsync" in events[link_index + 1 :]
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "accepted"
    assert list(tmp_path.glob(f".{spool_path.name}.*.admission")) == []


def test_recover_accepted_webhook_delivery_replays_once_and_marks_processed(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-recover.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "accepted_at": "2026-07-09T00:00:00+00:00",
            "event": "pull_request",
            "delivery": "delivery-recover",
            "body": '{"action":"opened"}',
        },
    )
    calls = []

    def fake_run_webhook_event_from_http(**kwargs):
        calls.append(kwargs)
        return 0, {"success": True, "result": {"action": "skipped", "reason": "already_reviewed"}}

    monkeypatch.setattr(automation, "_run_webhook_event_from_http", fake_run_webhook_event_from_http)
    kwargs = {
        "review_runner": lambda args, ctx=None: None,
        "config": "repos.json",
        "state": "state.json",
        "no_llm": False,
        "processing_lock": threading.Lock(),
        "processing_slot": threading.BoundedSemaphore(1),
    }

    first = automation._recover_accepted_webhook_deliveries(**kwargs)
    second = automation._recover_accepted_webhook_deliveries(**kwargs)

    completed = json.loads(spool_path.read_text(encoding="utf-8"))
    assert first == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert second == {"accepted": 0, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert len(calls) == 1
    assert calls[0]["body"] == b'{"action":"opened"}'
    assert calls[0]["event"] == "pull_request"
    assert calls[0]["delivery"] == "delivery-recover"
    assert calls[0]["force"] is False
    assert completed["status"] == "processed"
    assert completed["rc"] == 0
    assert completed["recovered"] is True
    assert completed["result"]["result"]["reason"] == "already_reviewed"


def test_recover_accepted_webhook_deliveries_stops_between_candidates(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    for delivery in ("delivery-a", "delivery-b"):
        automation._write_json_file(
            spool_dir / f"{delivery}.json",
            {
                "schema_version": 1,
                "status": "accepted",
                "event": "pull_request",
                "delivery": delivery,
                "body": '{"action":"opened"}',
            },
        )
    calls = []
    stop_checks = 0

    def should_stop():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks > 1

    monkeypatch.setattr(
        automation,
        "_run_webhook_event_from_http",
        lambda **kwargs: calls.append(kwargs) or (0, {"success": True}),
    )
    summary = automation._recover_accepted_webhook_deliveries(
        review_runner=lambda args, ctx=None: None,
        config=None,
        state=None,
        no_llm=False,
        processing_lock=threading.Lock(),
        processing_slot=threading.BoundedSemaphore(1),
        should_stop=should_stop,
    )

    assert summary == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert [call["delivery"] for call in calls] == ["delivery-a"]
    assert json.loads((spool_dir / "delivery-a.json").read_text())["status"] == "processed"
    assert json.loads((spool_dir / "delivery-b.json").read_text())["status"] == "accepted"


def test_recover_accepted_webhook_delivery_marks_invalid_record_failed(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-invalid.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "accepted_at": "2026-07-09T00:00:00+00:00",
            "event": "pull_request",
            "delivery": "delivery-invalid",
            "body": None,
        },
    )
    calls = []
    monkeypatch.setattr(automation, "_run_webhook_event_from_http", lambda **kwargs: calls.append(kwargs))

    summary = automation._recover_accepted_webhook_deliveries(
        review_runner=lambda args, ctx=None: None,
        config=None,
        state=None,
        no_llm=False,
        processing_lock=threading.Lock(),
        processing_slot=threading.BoundedSemaphore(1),
    )

    completed = json.loads(spool_path.read_text(encoding="utf-8"))
    assert summary == {"accepted": 1, "recovered": 0, "failed": 1, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert calls == []
    assert completed["status"] == "failed"
    assert completed["rc"] == 1
    assert completed["recovered"] is True
    assert "body" in completed["result"]["error"]


def test_recover_accepted_webhook_delivery_reports_unreadable_spool(monkeypatch, capsys, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    spool_dir.mkdir()
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "corrupt.json"
    spool_path.write_text("{not-json", encoding="utf-8")

    summary = automation._recover_accepted_webhook_deliveries(
        review_runner=lambda args, ctx=None: None,
        config=None,
        state=None,
        no_llm=False,
        processing_lock=threading.Lock(),
        processing_slot=threading.BoundedSemaphore(1),
    )

    logged = json.loads(capsys.readouterr().err)
    assert summary == {"accepted": 0, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 1}
    assert logged["action"] == "recovery_unreadable"
    assert logged["spool"] == str(spool_path)
    assert spool_path.read_text(encoding="utf-8") == "{not-json"


def test_recover_accepted_webhook_delivery_skips_locked_claim(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-locked.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-locked",
            "body": '{"action":"opened"}',
        },
    )
    calls = []
    monkeypatch.setattr(automation, "_run_webhook_event_from_http", lambda **kwargs: calls.append(kwargs) or (0, {"success": True}))
    held_lock = automation._acquire_delivery_recovery_lock(spool_path)
    assert held_lock is not None
    try:
        summary = automation._recover_accepted_webhook_deliveries(
            review_runner=lambda args, ctx=None: None,
            config=None,
            state=None,
            no_llm=False,
            processing_lock=threading.Lock(),
            processing_slot=threading.BoundedSemaphore(1),
        )
    finally:
        automation._release_advisory_lock(held_lock)

    assert summary == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 1, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert calls == []
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "accepted"


def test_recovery_respects_receiver_wide_processing_lock(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-overlap.json"
    automation._write_webhook_delivery_spool(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-overlap",
            "body": '{"action":"opened"}',
        },
    )
    calls = []
    processing_handle = automation._acquire_webhook_processing_lock(blocking=True)
    assert processing_handle is not None
    kwargs = {
        "review_runner": lambda args, ctx=None: None,
        "config": None,
        "state": None,
        "no_llm": True,
        "processing_lock": threading.Lock(),
        "processing_slot": threading.BoundedSemaphore(1),
    }
    monkeypatch.setattr(
        automation,
        "_run_webhook_event_from_http",
        lambda **event_kwargs: calls.append(event_kwargs) or (0, {"success": True, "result": {"action": "reviewed"}}),
    )

    locked = automation._recover_accepted_webhook_deliveries(**kwargs)
    automation._release_advisory_lock(processing_handle)
    recovered = automation._recover_accepted_webhook_deliveries(**kwargs)

    assert locked == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 1, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert recovered == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert len(calls) == 1
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "processed"


def test_recovery_does_not_hold_processing_fence_while_normal_worker_owns_slot(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-lock-order.json"
    automation._write_webhook_delivery_spool(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-lock-order",
            "body": '{"action":"opened"}',
        },
    )
    calls = []
    processing_slot = threading.BoundedSemaphore(1)
    processing_slot.acquire()
    kwargs = {
        "review_runner": lambda args, ctx=None: None,
        "config": None,
        "state": None,
        "no_llm": True,
        "processing_lock": threading.Lock(),
        "processing_slot": processing_slot,
    }
    monkeypatch.setattr(
        automation,
        "_run_webhook_event_from_http",
        lambda **event_kwargs: calls.append(event_kwargs) or (0, {"success": True, "result": {"action": "reviewed"}}),
    )

    locked = automation._recover_accepted_webhook_deliveries(**kwargs)
    processing_handle = automation._acquire_webhook_processing_lock(blocking=False)
    assert processing_handle is not None
    automation._release_advisory_lock(processing_handle)
    processing_slot.release()
    recovered = automation._recover_accepted_webhook_deliveries(**kwargs)

    assert locked == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 1, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert recovered == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert len(calls) == 1


def test_recover_accepted_webhook_delivery_leaves_record_for_retry_when_lock_fails(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-lock-error.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-lock-error",
            "body": '{"action":"opened"}',
        },
    )
    def fail_lock(path):
        raise OSError("lock unavailable")

    monkeypatch.setattr(automation, "_acquire_delivery_recovery_lock", fail_lock)

    summary = automation._recover_accepted_webhook_deliveries(
        review_runner=lambda args, ctx=None: None,
        config=None,
        state=None,
        no_llm=False,
        processing_lock=threading.Lock(),
        processing_slot=threading.BoundedSemaphore(1),
    )

    assert summary == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 1, "retryable": 0, "unreadable": 0}
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "accepted"


def test_recover_accepted_webhook_delivery_skips_missing_frozen_candidate(monkeypatch, capsys, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-missing.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-missing",
            "body": '{"action":"opened"}',
        },
    )
    monkeypatch.setattr(
        automation,
        "_acquire_delivery_recovery_lock",
        lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
    )

    summary = automation._recover_accepted_webhook_deliveries(
        review_runner=lambda args, ctx=None: None,
        config=None,
        state=None,
        no_llm=False,
        processing_lock=threading.Lock(),
        processing_slot=threading.BoundedSemaphore(1),
        spool_paths=[spool_path],
    )

    assert summary == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert json.loads(capsys.readouterr().err)["action"] == "recovery_missing"


def test_recover_accepted_webhook_delivery_retries_terminal_spool_write_failure(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-write-retry.json"
    automation._write_json_file(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-write-retry",
            "body": '{"action":"opened"}',
        },
    )
    run_calls = []
    write_calls = []
    real_write = automation._write_webhook_delivery_spool
    monkeypatch.setattr(
        automation,
        "_run_webhook_event_from_http",
        lambda **kwargs: run_calls.append(kwargs) or (0, {"success": True, "result": {"reason": "already_reviewed"}}),
    )

    def flaky_write(path, payload):
        write_calls.append(path)
        if len(write_calls) == 1:
            raise OSError("transient spool write failure")
        real_write(path, payload)

    monkeypatch.setattr(automation, "_write_webhook_delivery_spool", flaky_write)
    kwargs = {
        "review_runner": lambda args, ctx=None: None,
        "config": None,
        "state": None,
        "no_llm": False,
        "processing_lock": threading.Lock(),
        "processing_slot": threading.BoundedSemaphore(1),
    }

    first = automation._recover_accepted_webhook_deliveries(**kwargs)
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "accepted"
    second = automation._recover_accepted_webhook_deliveries(**kwargs)

    assert first == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 1, "unreadable": 0}
    assert second == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert len(run_calls) == 2
    assert all(call["force"] is False for call in run_calls)
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "processed"


def test_recover_accepted_webhook_delivery_retries_unexpected_event_exception(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    spool_path = spool_dir / "delivery-event-retry.json"
    automation._write_webhook_delivery_spool(
        spool_path,
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-event-retry",
            "body": '{"action":"opened"}',
        },
    )
    calls = []

    def flaky_event(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise OSError("transient watch-state persistence failure")
        return 0, {"success": True, "result": {"action": "reviewed"}}

    monkeypatch.setattr(automation, "_run_webhook_event_from_http", flaky_event)
    kwargs = {
        "review_runner": lambda args, ctx=None: None,
        "config": None,
        "state": None,
        "no_llm": False,
        "processing_lock": threading.Lock(),
        "processing_slot": threading.BoundedSemaphore(1),
    }

    first = automation._recover_accepted_webhook_deliveries(**kwargs)
    assert json.loads(spool_path.read_text(encoding="utf-8"))["status"] == "accepted"
    second = automation._recover_accepted_webhook_deliveries(**kwargs)

    assert first == {"accepted": 1, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 1, "unreadable": 0}
    assert second == {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}
    assert len(calls) == 2
    assert all(call["force"] is False for call in calls)


def test_webhook_serve_schedules_recovery_after_listener_bind(monkeypatch, capsys, tmp_path: Path):
    recovery_calls = []
    lifecycle = []
    recovery_done = threading.Event()
    spool_dir = tmp_path / "deliveries"
    spool_dir.mkdir()
    historical = spool_dir / "historical.json"
    historical.write_text("{}", encoding="utf-8")
    late_delivery = spool_dir / "late.json"

    class FakeHTTPServer:
        def __init__(self, address, handler):
            lifecycle.append(("init", address, handler))

        def serve_forever(self):
            lifecycle.append(("serve",))
            late_delivery.write_text("{}", encoding="utf-8")
            assert recovery_done.wait(timeout=2)

        def server_close(self):
            lifecycle.append(("close",))

    def fake_recovery(**kwargs):
        lifecycle.append(("recover",))
        recovery_calls.append(kwargs)
        recovery_done.set()
        return {"accepted": 2, "recovered": 2, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}

    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    monkeypatch.setattr(automation, "_recover_accepted_webhook_deliveries", fake_recovery)
    monkeypatch.setattr(automation, "HTTPServer", FakeHTTPServer)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=8787,
        path="/webhooks/github",
        config=str(tmp_path / "repos.json"),
        state=str(tmp_path / "state.json"),
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=True,
        no_llm=True,
        once=False,
        json=True,
    )

    rc = automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None)

    startup = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(recovery_calls) == 1
    assert recovery_calls[0]["config"] == str(tmp_path / "repos.json")
    assert recovery_calls[0]["state"] == str(tmp_path / "state.json")
    assert recovery_calls[0]["spool_paths"] == [historical]
    assert "force" not in recovery_calls[0]
    assert startup["recovery"] == {"scheduled": True, "candidates": 1}
    assert lifecycle[0][0] == "init"
    assert sorted(item[0] for item in lifecycle[1:-1]) == ["recover", "serve"]
    assert lifecycle[-1][0] == "close"


def test_webhook_serve_keeps_retrying_unresolved_startup_candidate_without_restart(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    spool_dir.mkdir()
    historical = spool_dir / "historical.json"
    historical.write_text("{}", encoding="utf-8")
    recovery_calls = []
    retry_delays = []
    servers = []

    class FakeHTTPServer:
        def __init__(self, address, handler):
            servers.append(self)

        def serve_forever(self):
            deadline = time.time() + 2
            while getattr(self, "recovery_summary", {}).get("attempts") != 6 and time.time() < deadline:
                time.sleep(0.01)
            assert getattr(self, "recovery_summary", {}).get("attempts") == 6

        def server_close(self):
            pass

    def fake_recovery(**kwargs):
        recovery_calls.append(kwargs)
        if len(recovery_calls) <= 3:
            return {"accepted": 1, "recovered": 0, "failed": 0, "locked": 1, "lock_errors": 0, "retryable": 0, "unreadable": 0}
        if len(recovery_calls) <= 5:
            return {"accepted": 1, "recovered": 0, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 1, "unreadable": 0}
        return {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}

    def record_retry(_stop_event, seconds):
        retry_delays.append(seconds)
        return False

    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    monkeypatch.setattr(automation, "_recover_accepted_webhook_deliveries", fake_recovery)
    monkeypatch.setattr(automation, "_wait_for_webhook_recovery_retry", record_retry)
    monkeypatch.setattr(automation, "HTTPServer", FakeHTTPServer)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=8787,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=False,
        json=True,
    )

    assert automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None) == 0

    assert len(recovery_calls) == 6
    assert retry_delays == [0.1, 0.2, 0.4, 0.8, 1.6]
    assert all(call["spool_paths"] == [historical] for call in recovery_calls)
    assert servers[0].recovery_summary == {
        "accepted": 1,
        "recovered": 1,
        "failed": 0,
        "locked": 0,
        "lock_errors": 0,
        "retryable": 0,
        "unreadable": 0,
        "attempts": 6,
    }


def test_webhook_serve_stops_recovery_retries_when_listener_exits(monkeypatch, tmp_path: Path):
    spool_dir = tmp_path / "deliveries"
    spool_dir.mkdir()
    historical = spool_dir / "historical.json"
    historical.write_text("{}", encoding="utf-8")
    recovery_calls = []
    servers = []

    class FakeHTTPServer:
        def __init__(self, address, handler):
            servers.append(self)

        def serve_forever(self):
            deadline = time.time() + 2
            while not getattr(self, "recovery_summary", {}).get("retrying") and time.time() < deadline:
                time.sleep(0.01)
            assert getattr(self, "recovery_summary", {}).get("retrying") is True

        def server_close(self):
            self.closed = True

    def locked_recovery(**kwargs):
        recovery_calls.append(kwargs)
        return {"accepted": 1, "recovered": 0, "failed": 0, "locked": 1, "lock_errors": 0, "retryable": 0, "unreadable": 0}

    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    monkeypatch.setattr(automation, "_recover_accepted_webhook_deliveries", locked_recovery)
    monkeypatch.setattr(automation, "HTTPServer", FakeHTTPServer)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=8787,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=False,
        json=True,
    )

    assert automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None) == 0
    calls_after_shutdown = len(recovery_calls)
    time.sleep(0.15)

    assert calls_after_shutdown == 1
    assert len(recovery_calls) == 1
    assert servers[0].closed is True
    assert servers[0].recovery_summary == {
        "accepted": 1,
        "recovered": 0,
        "failed": 0,
        "locked": 1,
        "lock_errors": 0,
        "retryable": 0,
        "unreadable": 0,
        "attempts": 1,
        "stopped": True,
    }


def test_webhook_serve_once_skips_historical_recovery(monkeypatch, capsys, tmp_path: Path):
    lifecycle = []

    class FakeHTTPServer:
        def __init__(self, address, handler):
            lifecycle.append("init")

        def handle_request(self):
            lifecycle.append("handle")
            self.last_request_success = True

        def server_close(self):
            lifecycle.append("close")

    def unexpected_recovery(**kwargs):
        raise AssertionError("--once must not recover historical deliveries")

    def unexpected_spool_scan():
        raise AssertionError("--once must not scan historical deliveries")

    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", unexpected_spool_scan)
    monkeypatch.setattr(automation, "_recover_accepted_webhook_deliveries", unexpected_recovery)
    monkeypatch.setattr(automation, "HTTPServer", FakeHTTPServer)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=8787,
        path="/webhooks/github",
        config=str(tmp_path / "repos.json"),
        state=str(tmp_path / "state.json"),
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=True,
        json=True,
    )

    rc = automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None)

    startup = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert startup["recovery"] == {"scheduled": False, "candidates": 0}
    assert lifecycle == ["init", "handle", "close"]


def test_webhook_serve_health_is_available_while_background_recovery_is_blocked(monkeypatch, tmp_path: Path):
    recovery_started = threading.Event()
    release_recovery = threading.Event()
    server_holder = []
    results = []
    original_http_server = automation.HTTPServer
    spool_dir = tmp_path / "deliveries"

    def blocked_recovery(**kwargs):
        recovery_started.set()
        assert release_recovery.wait(timeout=5)
        return {"accepted": 1, "recovered": 1, "failed": 0, "locked": 0, "lock_errors": 0, "retryable": 0, "unreadable": 0}

    def capture_http_server(*args, **kwargs):
        server = original_http_server(*args, **kwargs)
        server_holder.append(server)
        return server

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    monkeypatch.setattr(automation, "_recover_accepted_webhook_deliveries", blocked_recovery)
    monkeypatch.setattr(automation, "HTTPServer", capture_http_server)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=False,
        json=True,
    )
    receiver = threading.Thread(
        target=lambda: results.append(automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None)),
        daemon=True,
    )
    receiver.start()
    assert recovery_started.wait(timeout=2)

    deadline = time.time() + 2
    health_status = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.2) as response:
                health_status = response.status
                break
        except (OSError, urllib.error.URLError):
            time.sleep(0.02)

    assert server_holder
    server_holder[0].shutdown()
    time.sleep(1.1)
    assert receiver.is_alive()
    release_recovery.set()
    receiver.join(timeout=2)

    assert health_status == 200
    assert results == [0]
    assert not receiver.is_alive()


def test_cmd_watch_run_reviews_new_enabled_repo_pr_and_records_state(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    config.write_text(
        json.dumps(
            {
                "repos": {
                    "owner/repo": {
                        "enabled": True,
                        "postComment": True,
                        "graphContext": "auto",
                        "localRepo": str(tmp_path / "repo-checkout"),
                    },
                    "owner/disabled": {"enabled": False},
                }
            }
        )
    )
    calls = []

    def fake_run_gh_json(args, timeout=120):
        assert args[:2] == ["pr", "list"]
        assert args[args.index("--repo") + 1] == "owner/repo"
        assert args[args.index("--search") + 1] == "sort:updated-desc"
        return [
            {
                "number": 7,
                "title": "Fix bug",
                "headRefOid": "abc123",
                "isDraft": False,
                "updatedAt": "2026-07-01T00:00:00Z",
                "url": "https://github.com/owner/repo/pull/7",
            }
        ]

    def fake_run_review(args, ctx=None):
        calls.append(args)
        assert args.pr == "owner/repo#7"
        assert args.post_comment is True
        assert args.post_findings_only is True
        assert args.allow_truncated_post is False
        assert args.graph_context_auto is True
        assert args.local_repo == str(tmp_path / "repo-checkout")
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "abc123",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 1,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": True, "status": "collected"},
            "graph_context_auto_skipped": None,
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": {"action": "created"},
        }

    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)

    args = argparse.Namespace(
        pr_review_command="watch-run",
        config=str(config),
        state=str(state),
        repo=[],
        limit_per_repo=5,
        force=False,
        no_llm=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert len(calls) == 1
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["head_sha"] == "abc123"
    assert saved["reviews"]["owner/repo#7"]["findings"] == 1
    assert saved["reviews"]["owner/repo#7"]["graph_context"] == {"enabled": True, "status": "collected"}
    assert saved["reviews"]["owner/repo#7"]["graph_context_auto_skipped"] is None


def test_cmd_watch_run_skips_already_reviewed_head(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {"owner/repo#7": {"head_sha": "abc123", "success": True}}}))
    calls = []

    monkeypatch.setattr(
        core,
        "run_gh_json",
        lambda args, timeout=120: [
            {"number": 7, "title": "Fix bug", "headRefOid": "abc123", "isDraft": False, "url": "https://github.com/owner/repo/pull/7"}
        ],
    )
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args))

    args = argparse.Namespace(
        pr_review_command="watch-run",
        config=str(config),
        state=str(state),
        repo=[],
        limit_per_repo=5,
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert calls == []
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["head_sha"] == "abc123"


def test_cmd_watch_run_retries_failed_prior_head(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {"owner/repo#7": {"head_sha": "abc123", "success": False}}}))
    calls = []

    monkeypatch.setattr(
        core,
        "run_gh_json",
        lambda args, timeout=120: [
            {"number": 7, "title": "Fix bug", "headRefOid": "abc123", "isDraft": False, "url": "https://github.com/owner/repo/pull/7"}
        ],
    )

    def fake_run_review(args, ctx=None):
        calls.append(args)
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "abc123",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 0,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": False},
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)

    args = argparse.Namespace(
        pr_review_command="watch-run",
        config=str(config),
        state=str(state),
        repo=[],
        limit_per_repo=5,
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.pr for call in calls] == ["owner/repo#7"]
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["success"] is True


def test_cmd_watch_run_real_review_does_not_skip_prior_no_llm_smoke(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {"owner/repo#7": {"head_sha": "abc123", "success": True, "no_llm": True}}}))
    calls = []

    monkeypatch.setattr(
        core,
        "run_gh_json",
        lambda args, timeout=120: [
            {"number": 7, "title": "Fix bug", "headRefOid": "abc123", "isDraft": False, "url": "https://github.com/owner/repo/pull/7"}
        ],
    )

    def fake_run_review(args, ctx=None):
        calls.append(args)
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "abc123",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 0,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": False},
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)

    args = argparse.Namespace(
        pr_review_command="watch-run",
        config=str(config),
        state=str(state),
        repo=[],
        limit_per_repo=5,
        force=False,
        no_llm=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.pr for call in calls] == ["owner/repo#7"]
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["no_llm"] is False


def test_cmd_watch_run_records_actual_reviewed_head(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    monkeypatch.setattr(
        core,
        "run_gh_json",
        lambda args, timeout=120: [
            {"number": 7, "title": "Fix bug", "headRefOid": "listed123", "isDraft": False, "url": "https://github.com/owner/repo/pull/7"}
        ],
    )
    monkeypatch.setattr(
        pr_review_cli,
        "_run_review",
        lambda args, ctx=None: {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "reviewed456",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 0,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": False},
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        },
    )

    args = argparse.Namespace(
        pr_review_command="watch-run",
        config=str(config),
        state=str(state),
        repo=[],
        limit_per_repo=5,
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["head_sha"] == "reviewed456"
    assert saved["reviews"]["owner/repo#7"]["listed_head_sha"] == "listed123"


def _github_pr_webhook_payload(action: str = "synchronize", *, draft: bool = False, head: str = "abc123", repo: str = "owner/repo") -> dict:
    return {
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": {
            "number": 7,
            "title": "Fix bug",
            "draft": draft,
            "html_url": "https://github.com/owner/repo/pull/7",
            "updated_at": "2026-07-02T00:00:00Z",
            "head": {"sha": head},
        },
    }


def test_cmd_webhook_event_reviews_enabled_pull_request_and_records_event(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True, "postComment": False, "localRepo": str(tmp_path / "repo")}}}))
    payload_path.write_text(json.dumps(_github_pr_webhook_payload()))
    calls = []

    def fake_run_review(args, ctx=None):
        calls.append(args)
        assert args.pr == "owner/repo#7"
        assert args.post_comment is False
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "abc123",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 2,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": False},
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    monkeypatch.setattr(automation, "_fetch_current_pr_for_webhook", lambda repo, number: {
        "number": number,
        "title": "Fix bug",
        "headRefOid": "abc123",
        "isDraft": False,
        "updatedAt": "2026-07-02T00:00:00Z",
        "url": "https://github.com/owner/repo/pull/7",
    })
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-1",
        force=False,
        no_llm=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.pr for call in calls] == ["owner/repo#7"]
    saved = json.loads(state.read_text())
    record = saved["reviews"]["owner/repo#7"]
    assert record["head_sha"] == "abc123"
    assert record["findings"] == 2
    assert record["last_event"]["delivery"] == "delivery-1"
    assert record["last_event"]["action"] == "synchronize"


def test_cmd_webhook_event_matches_repo_case_insensitively(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True, "postComment": False}}}))
    payload_path.write_text(json.dumps(_github_pr_webhook_payload(repo="Owner/Repo")))
    calls = []
    monkeypatch.setattr(automation, "_fetch_current_pr_for_webhook", lambda repo, number: {
        "number": number,
        "headRefOid": "abc123",
        "isDraft": False,
        "state": "OPEN",
        "url": "https://github.com/owner/repo/pull/7",
    })

    def fake_run_review(args, ctx=None):
        calls.append(args)
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 7,
            "pr_ref": "owner/repo#7",
            "head_sha": "abc123",
            "verdict": "comment",
            "risk": "low",
            "mode": "balanced",
            "findings": 0,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "graph_context": {"enabled": False},
            "check_context": {},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-case",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.pr for call in calls] == ["owner/repo#7"]
    saved = json.loads(state.read_text())
    assert "owner/repo#7" in saved["reviews"]
    assert "Owner/Repo#7" not in saved["reviews"]


def test_cmd_webhook_event_ignores_unsupported_event_without_payload_or_config(tmp_path: Path):
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(tmp_path / "missing-repos.json"),
        state=str(tmp_path / "state.json"),
        payload=str(tmp_path / "missing-payload.json"),
        event="ping",
        delivery="delivery-ping",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert not (tmp_path / "state.json").exists()


def test_cmd_webhook_event_skips_unsupported_action_without_state_write(monkeypatch, tmp_path: Path):
    config = tmp_path / "missing-repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(_github_pr_webhook_payload(action="edited")))
    calls = []
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args))
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-2",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert calls == []
    assert not state.exists()


def test_cmd_webhook_event_skips_already_reviewed_head(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {"owner/repo#7": {"head_sha": "abc123", "success": True}}}))
    payload_path.write_text(json.dumps(_github_pr_webhook_payload(head="abc123")))
    calls = []
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args))
    monkeypatch.setattr(automation, "_fetch_current_pr_for_webhook", lambda repo, number: {
        "number": number,
        "headRefOid": "abc123",
        "isDraft": False,
        "url": "https://github.com/owner/repo/pull/7",
    })
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-3",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert calls == []
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["head_sha"] == "abc123"


def test_webhook_signature_validation_accepts_only_matching_sha256():
    body = b'{"zen":"Keep it logically awesome."}'
    secret = "super-secret"
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert automation._valid_github_signature(secret, body, good)
    assert not automation._valid_github_signature(secret, body, "sha256=bad")
    assert not automation._valid_github_signature(secret, body, None)
    assert not automation._valid_github_signature(secret, body, good.replace("sha256=", "sha1="))


def test_webhook_secret_prefers_file_then_env(monkeypatch, tmp_path: Path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("file-secret\n")
    monkeypatch.setenv("CUSTOM_WEBHOOK_SECRET", "env-secret")

    assert automation._resolve_webhook_secret(argparse.Namespace(secret="direct", secret_file=None, secret_env="CUSTOM_WEBHOOK_SECRET")) == "direct"
    assert automation._resolve_webhook_secret(argparse.Namespace(secret=None, secret_file=str(secret_file), secret_env="CUSTOM_WEBHOOK_SECRET")) == "file-secret"
    assert automation._resolve_webhook_secret(argparse.Namespace(secret=None, secret_file=None, secret_env="CUSTOM_WEBHOOK_SECRET")) == "env-secret"


def test_webhook_secret_rejects_empty_values(monkeypatch, tmp_path: Path):
    empty_file = tmp_path / "empty-secret.txt"
    empty_file.write_text("  \n")
    monkeypatch.delenv("EMPTY_WEBHOOK_SECRET", raising=False)

    with pytest.raises(ValueError, match="must not be empty"):
        automation._resolve_webhook_secret(argparse.Namespace(secret="  ", secret_file=None, secret_env="EMPTY_WEBHOOK_SECRET"))
    with pytest.raises(ValueError, match="must not be empty"):
        automation._resolve_webhook_secret(argparse.Namespace(secret=None, secret_file=str(empty_file), secret_env="EMPTY_WEBHOOK_SECRET"))
    with pytest.raises(ValueError, match="missing webhook secret"):
        automation._resolve_webhook_secret(argparse.Namespace(secret=None, secret_file=None, secret_env="EMPTY_WEBHOOK_SECRET"))


def test_cmd_enable_writes_repo_config_and_secret(capsys, tmp_path: Path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"repos": {"Owner/Repo": {"post_comment": True, "graph_context": "off", "local_repo": "/old"}}}))
    secret_file = tmp_path / "webhook-secret"
    local_repo = tmp_path / "checkout"
    local_repo.mkdir()
    graph_binary = tmp_path / "node-bin" / "codegraph"
    graph_binary.parent.mkdir()
    graph_binary.write_text("#!/bin/sh\n", encoding="utf-8")
    graph_binary.chmod(0o755)
    args = argparse.Namespace(
        pr_review_command="enable",
        repo="https://github.com/owner/repo",
        local_repo=str(local_repo),
        config=str(config),
        secret_file=str(secret_file),
        webhook_url="https://laptop.example.ts.net/webhooks/github",
        post_comment=False,
        review_drafts=False,
        graph_context="auto",
        graph_context_binary=str(graph_binary),
        mode="balanced",
        max_diff_chars=120_000,
        print_secret=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(config.read_text())
    assert list(saved["repos"]) == ["Owner/Repo"]
    secret = secret_file.read_text().strip()
    assert payload["repo"] == "Owner/Repo"
    assert payload["secret_created"] is True
    assert payload["secret_file"] == str(secret_file)
    assert payload["github"]["payload_url"] == "https://laptop.example.ts.net/webhooks/github"
    assert "secret" not in payload
    assert len(secret) >= 40
    assert (secret_file.stat().st_mode & 0o777) == 0o600
    assert saved["repos"]["Owner/Repo"] == {
        "enabled": True,
        "graphContext": "auto",
        "graphContextBinary": str(graph_binary.absolute()),
        "localRepo": str(local_repo.resolve()),
        "maxDiffChars": 120000,
        "mode": "balanced",
        "postComment": False,
        "postFindingsOnly": True,
        "reviewDrafts": False,
    }
    _, normalized = automation._load_watch_config(str(config))
    assert normalized["repos"]["Owner/Repo"]["local_repo"] == str(local_repo.resolve())
    assert normalized["repos"]["Owner/Repo"]["graph_context_binary"] == str(graph_binary.absolute())
    assert str(graph_binary.absolute()) in payload["commands"]["graph_setup"]


def test_cmd_enable_preserves_existing_settings_when_flags_omitted(capsys, tmp_path: Path):
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repos": {
                    "owner/repo": {
                        "enabled": True,
                        "postComment": True,
                        "postFindingsOnly": False,
                        "reviewDrafts": True,
                        "graphContext": "on",
                        "graphContextBinary": "/stable/node-bin/codegraph",
                        "mode": "strict",
                        "localRepo": str(tmp_path / "existing-checkout"),
                        "maxDiffChars": 7777,
                    }
                }
            }
        )
    )
    local_repo = tmp_path / "existing-checkout"
    local_repo.mkdir()
    secret_file = tmp_path / "secret with spaces.txt"
    args = argparse.Namespace(
        pr_review_command="enable",
        repo="owner/repo",
        local_repo=None,
        config=str(config),
        secret_file=str(secret_file),
        webhook_url=None,
        post_comment=None,
        post_findings_only=None,
        review_drafts=None,
        graph_context=None,
        mode=None,
        max_diff_chars=None,
        print_secret=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    saved = json.loads(config.read_text())
    assert saved["repos"]["owner/repo"]["postComment"] is True
    assert saved["repos"]["owner/repo"]["postFindingsOnly"] is False
    assert saved["repos"]["owner/repo"]["reviewDrafts"] is True
    assert saved["repos"]["owner/repo"]["graphContext"] == "on"
    assert saved["repos"]["owner/repo"]["graphContextBinary"] == "/stable/node-bin/codegraph"
    assert saved["repos"]["owner/repo"]["mode"] == "strict"
    assert saved["repos"]["owner/repo"]["maxDiffChars"] == 7777
    assert "'" in payload["commands"]["serve"]
    assert payload["commands"]["graph_setup"].startswith("hermes pr-review graph-setup --local-repo ")


def test_cmd_enable_can_clear_existing_graph_binary(capsys, tmp_path: Path):
    local_repo = tmp_path / "checkout"
    local_repo.mkdir()
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repos": {
                    "owner/repo": {
                        "enabled": True,
                        "graphContext": "auto",
                        "graphContextBinary": "/old/codegraph",
                        "localRepo": str(local_repo),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        repo="owner/repo",
        local_repo=None,
        config=str(config),
        secret_file=str(tmp_path / "secret"),
        webhook_url=None,
        post_comment=None,
        post_findings_only=None,
        review_drafts=None,
        graph_context=None,
        graph_context_binary=None,
        clear_graph_context_binary=True,
        mode=None,
        max_diff_chars=None,
        print_secret=False,
        json=True,
    )

    rc = automation.cmd_enable(args)

    assert rc == 0
    _ = capsys.readouterr()
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert "graphContextBinary" not in saved["repos"]["owner/repo"]


def test_cmd_enable_fails_before_writing_config_when_secret_invalid(capsys, tmp_path: Path):
    config = tmp_path / "repos.json"
    secret_file = tmp_path / "webhook-secret"
    local_repo = tmp_path / "checkout"
    local_repo.mkdir()
    secret_file.write_text("\n")
    args = argparse.Namespace(
        pr_review_command="enable",
        repo="owner/repo",
        local_repo=str(local_repo),
        config=str(config),
        secret_file=str(secret_file),
        webhook_url=None,
        post_comment=False,
        review_drafts=False,
        graph_context="auto",
        mode="balanced",
        max_diff_chars=120_000,
        print_secret=False,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert "empty" in payload["error"]
    assert not config.exists()


def test_ensure_webhook_secret_tightens_existing_permissions(tmp_path: Path):
    secret_file = tmp_path / "webhook-secret"
    secret_file.write_text("existing-secret\n")
    os.chmod(secret_file, 0o644)

    path, secret, created = automation._ensure_webhook_secret(str(secret_file))

    assert path == secret_file.resolve()
    assert secret == "existing-secret"
    assert created is False
    assert (secret_file.stat().st_mode & 0o777) == 0o600


def test_ensure_webhook_secret_rejects_directory_before_chmod(tmp_path: Path):
    secret_dir = tmp_path / "secrets-dir"
    secret_dir.mkdir()
    os.chmod(secret_dir, 0o755)

    with pytest.raises(ValueError, match="not a regular file"):
        automation._ensure_webhook_secret(str(secret_dir))

    assert (secret_dir.stat().st_mode & 0o777) == 0o755


def test_ensure_webhook_secret_rejects_symlink_before_chmod(tmp_path: Path):
    target = tmp_path / "target-secret"
    target.write_text("existing-secret\n")
    os.chmod(target, 0o644)
    secret_link = tmp_path / "secret-link"
    secret_link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        automation._ensure_webhook_secret(str(secret_link))

    assert (target.stat().st_mode & 0o777) == 0o644


def test_cmd_status_reports_registry_secret_state_and_deliveries(capsys, monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "watch-state.json"
    secret_file = tmp_path / "webhook-secret"
    deliveries = tmp_path / "deliveries"
    deliveries.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config.write_text(
        json.dumps(
            {
                "repos": {
                    "owner/repo": {
                        "enabled": True,
                        "postComment": True,
                        "postFindingsOnly": True,
                        "reviewDrafts": True,
                        "graphContext": "auto",
                        "localRepo": str(checkout),
                        "maxDiffChars": 7777,
                    }
                }
            }
        )
    )
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": {
                    "owner/repo#7": {
                        "head_sha": "abc123",
                        "reviewed_at": "2026-07-06T00:00:00+00:00",
                        "success": True,
                        "no_llm": False,
                        "findings": 0,
                        "risk": "low",
                        "graph_context": {"enabled": True, "provider": "codegraph", "status": "collected"},
                        "graph_context_auto_skipped": None,
                    }
                },
            }
        )
    )
    secret_file.write_text("secret-value\n")
    os.chmod(secret_file, 0o600)

    def fake_run(cmd, **kwargs):
        assert cmd == ["gh", "api", "repos/owner/repo/hooks/12345/deliveries", "-X", "GET", "-f", "per_page=3"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps([
                {
                    "id": 111,
                    "event": "pull_request",
                    "action": "synchronize",
                    "status": "OK",
                    "status_code": 204,
                    "delivered_at": "2026-07-06T00:00:02Z",
                    "redelivery": False,
                },
                {
                    "id": 110,
                    "event": "pull_request",
                    "action": "opened",
                    "status": "OK",
                    "status_code": 202,
                    "delivered_at": "2026-07-06T00:00:01Z",
                    "redelivery": False,
                },
            ]),
            stderr="",
        )

    monkeypatch.setattr("plugins.pr_review.automation.subprocess.run", fake_run)
    monkeypatch.setattr(
        graph_context,
        "codegraph_health",
        lambda **_kwargs: {
            "healthy": True,
            "reason": "ready for graph-context-auto",
            "provider": "codegraph",
            "binary_name": "codegraph",
            "head": "abc123",
            "index": {"exists": True},
            "status": {"initialized": True, "fileCount": 8, "nodeCount": 420, "edgeCount": 981, "languages": ["python"]},
            "checkout": {"clean": True, "dirty_paths": []},
        },
    )
    (deliveries / "delivery-1.json").write_text(
        json.dumps(
            {
                "status": "processed",
                "event": "pull_request",
                "delivery": "delivery-1",
                "accepted_at": "2026-07-06T00:00:00+00:00",
                "processed_at": "2026-07-06T00:00:01+00:00",
                "rc": 0,
                "result": {"result": {"action": "reviewed"}},
            }
        )
    )
    args = argparse.Namespace(
        pr_review_command="status",
        config=str(config),
        state=str(state),
        secret_file=str(secret_file),
        deliveries_dir=str(deliveries),
        repo=[],
        receiver_url="http://127.0.0.1:1/healthz",
        skip_receiver=True,
        receiver_timeout=0.1,
        recent_deliveries=5,
        github_repo="owner/repo",
        github_hook_id="12345",
        github_deliveries=3,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["repo_count"] == 1
    assert payload["repos"][0]["repo"] == "owner/repo"
    assert payload["repos"][0]["post_comment"] is True
    assert payload["repos"][0]["post_findings_only"] is True
    assert payload["repos"][0]["review_drafts"] is True
    assert payload["repos"][0]["graph_context"] == "auto"
    assert payload["repos"][0]["graph_health"]["healthy"] is True
    assert payload["repos"][0]["graph_health"]["reason"] == "ready for graph-context-auto"
    assert payload["repos"][0]["graph_health"]["index"]["nodes"] == 420
    assert payload["repos"][0]["live_graph"]["status"] == "ok"
    assert payload["repos"][0]["live_graph"]["used"] is True
    assert payload["repos"][0]["live_graph"]["provider"] == "codegraph"
    assert payload["repos"][0]["local_repo"] == str(checkout)
    assert payload["repos"][0]["max_diff_chars"] == 7777
    assert payload["repos"][0]["last_review_key"] == "owner/repo#7"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["secret"]["status"] == "ok"
    assert checks["secret"]["mode"] == "0o600"
    assert checks["deliveries"]["counts"] == {"processed": 1}
    assert checks["deliveries"]["recent"][0]["result_action"] == "reviewed"
    assert checks["github_hook"]["status"] == "ok"
    assert checks["graph"]["status"] == "ok"
    assert checks["graph_live"]["status"] == "ok"
    assert checks["github_hook"]["latest"] == {
        "id": 111,
        "event": "pull_request",
        "action": "synchronize",
        "status": "OK",
        "status_code": 204,
        "delivered_at": "2026-07-06T00:00:02Z",
        "redelivery": False,
    }
    assert checks["receiver"]["status"] == "skipped"
    assert payload["next_steps"] == ["Status is clean; run hermes pr-review watch-run --json or keep the webhook receiver running"]


def test_repo_graph_health_uses_configured_binary(monkeypatch, tmp_path: Path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    def fake_health(**kwargs):
        calls.append(kwargs)
        return {
            "healthy": True,
            "reason": "ready",
            "provider": "codegraph",
            "binary_name": "/stable/node-bin/codegraph",
            "head": "abc123",
            "index": {"exists": True},
            "status": {"initialized": True},
            "checkout": {"clean": True, "dirty_paths": []},
        }

    monkeypatch.setattr(graph_context, "codegraph_health", fake_health)

    result = automation._repo_graph_health(
        {"graph_context": "auto", "local_repo": str(checkout), "graph_context_binary": "/stable/node-bin/codegraph"}
    )

    assert calls == [{"local_repo": str(checkout), "binary": "/stable/node-bin/codegraph", "sync": False}]
    assert result["healthy"] is True
    assert "--graph-context-binary /stable/node-bin/codegraph" in result["setup_command"]


def test_repo_live_graph_status_requires_collected_outcome():
    cfg = {"graph_context": "auto"}
    failed = automation._repo_live_graph_status(
        cfg,
        {
            "head_sha": "abc123",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "success": True,
            "no_llm": False,
            "graph_context": {"enabled": True, "provider": "codegraph", "status": "failed"},
            "graph_context_auto_skipped": None,
        },
    )
    missing = automation._repo_live_graph_status(
        cfg,
        {
            "head_sha": "abc123",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "success": True,
            "no_llm": False,
            "graph_context": {"enabled": True, "provider": "codegraph"},
            "graph_context_auto_skipped": None,
        },
    )

    assert failed["status"] == "warn"
    assert failed["used"] is False
    assert failed["graph_status"] == "failed"
    assert failed["reason"] == "latest review graph context status was failed"
    assert missing["status"] == "skipped"
    assert missing["used"] is None
    assert missing["reason"] == "latest review graph outcome is missing collection status"


def test_repo_live_graph_status_rejects_failed_and_no_llm_reviews():
    cfg = {"graph_context": "auto"}
    collected = {"enabled": True, "provider": "codegraph", "status": "collected"}
    failed_review = automation._repo_live_graph_status(
        cfg,
        {
            "head_sha": "failed123",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "success": False,
            "no_llm": False,
            "graph_context": collected,
            "graph_context_auto_skipped": None,
        },
    )
    smoke_review = automation._repo_live_graph_status(
        cfg,
        {
            "head_sha": "smoke123",
            "reviewed_at": "2026-07-10T00:00:00+00:00",
            "success": True,
            "no_llm": True,
            "graph_context": collected,
            "graph_context_auto_skipped": None,
        },
    )

    assert failed_review["status"] == "warn"
    assert failed_review["used"] is False
    assert failed_review["reason"] == "latest live review failed"
    assert smoke_review["status"] == "skipped"
    assert smoke_review["used"] is None
    assert smoke_review["reason"] == "latest graph collection was a --no-llm smoke, not a completed model review"


def test_cmd_status_warns_when_latest_live_review_fell_back(capsys, monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "watch-state.json"
    deliveries = tmp_path / "deliveries"
    deliveries.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config.write_text(
        json.dumps({"repos": {"owner/repo": {"enabled": True, "graphContext": "auto", "localRepo": str(checkout)}}}),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviews": {
                    "owner/repo#9": {
                        "head_sha": "def456",
                        "reviewed_at": "2026-07-10T00:00:00+00:00",
                        "success": True,
                        "no_llm": False,
                        "graph_context": None,
                        "graph_context_auto_skipped": "codegraph binary not found",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        automation,
        "_repo_graph_health",
        lambda _cfg: {"enabled": True, "healthy": True, "status": "ok", "mode": "auto", "provider": "codegraph"},
    )
    args = argparse.Namespace(
        pr_review_command="status",
        config=str(config),
        state=str(state),
        secret_file=str(tmp_path / "missing-secret"),
        deliveries_dir=str(deliveries),
        repo=[],
        skip_receiver=True,
        recent_deliveries=5,
        github_repo=None,
        github_hook_id=None,
        github_deliveries=5,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["repos"][0]["live_graph"] == {
        "status": "warn",
        "enabled": True,
        "used": False,
        "reason": "codegraph binary not found",
        "reviewed_at": "2026-07-10T00:00:00+00:00",
        "head_sha": "def456",
    }
    assert checks["graph_live"]["status"] == "warn"
    assert any("--graph-context-binary /path/to/codegraph" in step for step in payload["next_steps"])


def test_status_next_steps_do_not_blame_launcher_for_index_fallback(tmp_path: Path):
    summary = {
        "status": "warn",
        "repo_count": 1,
        "enabled_repo_count": 1,
        "checks": [],
        "repos": [
            {
                "repo": "owner/repo",
                "local_repo": str(tmp_path / "checkout"),
                "graph_context_binary": "/stable/node-bin/codegraph",
                "graph_health": {"status": "ok"},
                "live_graph": {"status": "warn", "reason": "missing .codegraph index"},
            }
        ],
    }

    steps = automation._status_next_steps(summary)

    assert any("Inspect live graph fallback for owner/repo: missing .codegraph index" in step for step in steps)
    assert any("hermes pr-review graph-health --local-repo" in step for step in steps)
    assert any("--graph-context-binary /stable/node-bin/codegraph" in step for step in steps)
    assert not any("--graph-context-binary /path/to/codegraph" in step for step in steps)


def test_cmd_status_reports_graph_setup_next_step(capsys, monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "watch-state.json"
    secret_file = tmp_path / "webhook-secret"
    deliveries = tmp_path / "deliveries"
    checkout = tmp_path / "checkout"
    deliveries.mkdir()
    checkout.mkdir()
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True, "graphContext": "auto", "localRepo": str(checkout)}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {}}))
    secret_file.write_text("secret-value\n")
    os.chmod(secret_file, 0o600)
    monkeypatch.setattr(
        graph_context,
        "codegraph_health",
        lambda **_kwargs: {
            "healthy": False,
            "reason": "missing .codegraph index",
            "provider": "codegraph",
            "index": {"exists": False},
            "status": {"initialized": False},
            "checkout": {"clean": True, "dirty_paths": []},
        },
    )
    args = argparse.Namespace(
        pr_review_command="status",
        config=str(config),
        state=str(state),
        secret_file=str(secret_file),
        deliveries_dir=str(deliveries),
        repo=[],
        receiver_url="http://127.0.0.1:1/healthz",
        skip_receiver=True,
        receiver_timeout=0.1,
        recent_deliveries=5,
        github_repo=None,
        github_hook_id=None,
        github_deliveries=5,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warn"
    graph = payload["repos"][0]["graph_health"]
    assert graph["healthy"] is False
    assert graph["reason"] == "missing .codegraph index"
    assert graph["next_step"] == f"hermes pr-review graph-setup --local-repo {checkout} --install-missing"
    assert any("Prepare graph context for owner/repo" in step for step in payload["next_steps"])


def test_cmd_status_fails_for_invalid_secret_path(capsys, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "watch-state.json"
    secret_dir = tmp_path / "secret-dir"
    deliveries = tmp_path / "deliveries"
    secret_dir.mkdir()
    deliveries.mkdir()
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {}}))
    args = argparse.Namespace(
        pr_review_command="status",
        config=str(config),
        state=str(state),
        secret_file=str(secret_dir),
        deliveries_dir=str(deliveries),
        repo=[],
        receiver_url="http://127.0.0.1:1/healthz",
        skip_receiver=True,
        receiver_timeout=0.1,
        recent_deliveries=5,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert payload["status"] == "fail"
    assert checks["secret"]["reason"] == "secret path is not a regular file"
    assert payload["next_steps"] == ["Fix the webhook secret path before serving webhooks: secret path is not a regular file"]


def test_cmd_status_prints_next_steps_for_empty_setup(capsys, tmp_path: Path):
    args = argparse.Namespace(
        pr_review_command="status",
        config=str(tmp_path / "repos.json"),
        state=str(tmp_path / "watch-state.json"),
        secret_file=str(tmp_path / "webhook-secret"),
        deliveries_dir=str(tmp_path / "deliveries"),
        repo=[],
        receiver_url="http://127.0.0.1:1/healthz",
        skip_receiver=True,
        receiver_timeout=0.1,
        recent_deliveries=5,
        json=False,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    output = capsys.readouterr().out
    assert "status  : warn" in output
    assert "Next steps:" in output
    assert "hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout" in output


def test_run_webhook_event_from_http_returns_summary_without_stdout_capture():
    rc, result = automation._run_webhook_event_from_http(
        review_runner=lambda args, ctx=None: None,
        body=b'{"action":"ping"}',
        event="ping",
        delivery="delivery-http",
        config="repos.json",
        state="state.json",
        force=False,
        no_llm=True,
    )

    assert rc == 0
    assert result["success"] is True
    assert result["result"]["action"] == "ignored"
    assert result["result"]["reason"] == "unsupported_event"
    assert result["result"]["event"]["event"] == "ping"
    assert result["result"]["event"]["delivery"] == "delivery-http"
    assert result["state"].endswith("state.json")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(port: int, path: str = "/healthz") -> None:
    deadline = time.time() + 5
    url = f"http://127.0.0.1:{port}{path}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.25):
                return
        except Exception:
            time.sleep(0.05)
    raise AssertionError(f"server did not become ready on {url}")


def _open_with_retry(request: urllib.request.Request, *, timeout: float = 5):
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05)
    raise AssertionError(f"request did not succeed before timeout: {last_exc}")


def _signed_github_request(url: str, body: bytes, secret: str, *, event: str = "ping", delivery: str = "delivery-test") -> urllib.request.Request:
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": signature,
        },
    )


def test_webhook_serve_once_accepts_signed_request(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: tmp_path / "deliveries")
    port = _free_local_port()
    args = argparse.Namespace(
        pr_review_command="webhook-serve",
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=True,
        json=True,
    )
    thread = threading.Thread(target=lambda: pr_review_cli.pr_review_command(args), daemon=True)
    thread.start()

    head_request = urllib.request.Request(f"http://127.0.0.1:{port}/healthz?probe=1", method="HEAD")
    with _open_with_retry(head_request, timeout=5) as head_response:
        head_body = head_response.read()
    assert head_response.status == 200
    assert head_body == b""

    with _open_with_retry(urllib.request.Request(f"http://127.0.0.1:{port}/healthz", method="GET"), timeout=5) as health_response:
        health_payload = json.loads(health_response.read())
    assert health_response.status == 200
    assert health_payload["success"] is True

    body = b'{"zen":"smoke"}'
    request = _signed_github_request(f"http://127.0.0.1:{port}/webhooks/github", body, "test-secret", delivery="delivery-http-e2e")
    with _open_with_retry(request, timeout=5) as response:
        payload = json.loads(response.read())

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert response.status == 200
    assert payload["success"] is True
    assert payload["result"]["action"] == "ignored"
    assert payload["result"]["reason"] == "unsupported_event"
    assert payload["result"]["event"]["delivery"] == "delivery-http-e2e"


def test_webhook_serve_once_processes_explicit_accepted_duplicate(monkeypatch, tmp_path: Path):
    port = _free_local_port()
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    persisted_body = json.dumps(_github_pr_webhook_payload(action="opened", head="persisted-once")).encode()
    automation._write_webhook_delivery_spool(
        spool_dir / "delivery-once-duplicate.json",
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-once-duplicate",
            "body": persisted_body.decode(),
        },
    )
    calls = []
    processing_lock_modes = []
    real_acquire_processing_lock = automation._acquire_webhook_processing_lock

    def tracking_processing_lock(*, blocking):
        processing_lock_modes.append(blocking)
        return real_acquire_processing_lock(blocking=blocking)

    monkeypatch.setattr(automation, "_acquire_webhook_processing_lock", tracking_processing_lock)
    monkeypatch.setattr(
        automation,
        "_run_webhook_event_from_http",
        lambda **kwargs: calls.append(kwargs) or (0, {"success": True, "result": {"action": "reviewed"}}),
    )
    args = argparse.Namespace(
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=True,
        no_llm=True,
        once=True,
        json=True,
    )
    thread = threading.Thread(target=lambda: automation.cmd_webhook_serve(args, review_runner=lambda review_args, ctx=None: None), daemon=True)
    thread.start()
    retry_body = json.dumps(_github_pr_webhook_payload(action="opened", head="retry-once")).encode()
    request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        retry_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-once-duplicate",
    )

    with _open_with_retry(request, timeout=5) as response:
        payload = json.loads(response.read())
    thread.join(timeout=5)

    assert response.status == 200
    assert payload["success"] is True
    assert len(calls) == 1
    assert processing_lock_modes == [True]
    assert calls[0]["body"] == persisted_body
    assert calls[0]["force"] is False
    assert json.loads((spool_dir / "delivery-once-duplicate.json").read_text())["status"] == "processed"
    assert not thread.is_alive()


def test_webhook_serve_rejects_missing_event_header():
    port = _free_local_port()
    args = argparse.Namespace(
        pr_review_command="webhook-serve",
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=True,
        json=True,
    )
    thread = threading.Thread(target=lambda: pr_review_cli.pr_review_command(args), daemon=True)
    thread.start()

    body = b"{}"
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/github",
        data=body,
        method="POST",
        headers={"X-Hub-Signature-256": signature},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _open_with_retry(request, timeout=5)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert excinfo.value.code == 400


def test_webhook_serve_rejects_bad_signature(monkeypatch):
    port = _free_local_port()
    calls = []
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args) or {})
    args = argparse.Namespace(
        pr_review_command="webhook-serve",
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=None,
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=False,
        no_llm=True,
        once=True,
        json=True,
    )
    thread = threading.Thread(target=lambda: pr_review_cli.pr_review_command(args), daemon=True)
    thread.start()

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/github",
        data=b"{}",
        method="POST",
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=bad"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _open_with_retry(request, timeout=5)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert excinfo.value.code == 401
    assert calls == []


def test_webhook_serve_prequeue_ignores_unsupported_pull_request_action_when_busy(monkeypatch, tmp_path: Path):
    port = _free_local_port()
    spool_dir = tmp_path / "deliveries"
    monkeypatch.setattr(automation, "_webhook_delivery_spool_dir", lambda: spool_dir)
    worker_started = threading.Event()
    release_worker = threading.Event()
    calls = []
    processing_lock_modes = []
    real_acquire_processing_lock = automation._acquire_webhook_processing_lock

    def tracking_processing_lock(*, blocking):
        thread_name = threading.current_thread().name
        processing_lock_modes.append((thread_name, blocking))
        if thread_name == "pr-review-webhook-delivery-review" and sum(name == thread_name for name, _ in processing_lock_modes) == 1:
            raise OSError("transient processing lock failure")
        return real_acquire_processing_lock(blocking=blocking)

    def fake_run_webhook_event_from_http(**kwargs):
        calls.append(kwargs)
        worker_started.set()
        release_worker.wait(timeout=5)
        return 0, {"success": True, "result": {"action": "reviewed"}}

    monkeypatch.setattr(automation, "_acquire_webhook_processing_lock", tracking_processing_lock)
    monkeypatch.setattr(automation, "_run_webhook_event_from_http", fake_run_webhook_event_from_http)
    args = argparse.Namespace(
        pr_review_command="webhook-serve",
        host="127.0.0.1",
        port=port,
        path="/webhooks/github",
        config=None,
        state=str(tmp_path / "state.json"),
        secret="test-secret",
        secret_file=None,
        secret_env="MISSING_SECRET_ENV",
        max_body_bytes=1_000_000,
        read_timeout=2.0,
        force=True,
        no_llm=True,
        once=False,
        json=True,
    )
    thread = threading.Thread(target=lambda: pr_review_cli.pr_review_command(args), daemon=True)
    thread.start()
    _wait_for_http(port)

    persisted_body = json.dumps(_github_pr_webhook_payload(action="opened", head="persisted-head")).encode()
    automation._write_webhook_delivery_spool(
        spool_dir / "delivery-review.json",
        {
            "schema_version": 1,
            "status": "accepted",
            "event": "pull_request",
            "delivery": "delivery-review",
            "body": persisted_body.decode(),
        },
    )
    review_body = json.dumps(_github_pr_webhook_payload(action="opened", head="review-head")).encode()
    review_request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        review_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-review",
    )
    with _open_with_retry(review_request, timeout=5) as response:
        accepted = json.loads(response.read())
    assert response.status == 202
    assert accepted["action"] == "accepted"
    assert worker_started.wait(timeout=5)
    normal_lock_attempts = [(name, blocking) for name, blocking in processing_lock_modes if name == "pr-review-webhook-delivery-review"]
    assert len(normal_lock_attempts) >= 2
    assert all(blocking is False for _, blocking in normal_lock_attempts)

    duplicate_ignored_body = json.dumps(_github_pr_webhook_payload(action="edited", head="ignored-duplicate")).encode()
    duplicate_ignored_request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        duplicate_ignored_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-review",
    )
    with _open_with_retry(duplicate_ignored_request, timeout=5) as duplicate_ignored_response:
        duplicate_ignored_payload = json.loads(duplicate_ignored_response.read())
    preserved_accepted = json.loads((spool_dir / "delivery-review.json").read_text())
    assert duplicate_ignored_response.status == 202
    assert duplicate_ignored_payload["action"] == "duplicate"
    assert duplicate_ignored_payload["status"] == "accepted"
    assert preserved_accepted["status"] == "accepted"
    assert preserved_accepted["body"] == persisted_body.decode()

    edited_body = json.dumps(_github_pr_webhook_payload(action="edited", head="edited-head")).encode()
    edited_request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        edited_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-edited",
    )
    with _open_with_retry(edited_request, timeout=5) as edited_response:
        edited_payload = json.loads(edited_response.read())
    assert edited_response.status == 202
    assert edited_payload["action"] == "ignored"
    assert edited_payload["reason"] == "unsupported_action"

    busy_body = json.dumps(_github_pr_webhook_payload(action="synchronize", head="busy-head")).encode()
    busy_request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        busy_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-busy",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _open_with_retry(busy_request, timeout=5)
    assert excinfo.value.code == 503
    busy_payload = json.loads(excinfo.value.read())
    assert busy_payload["error"] == "review_queue_busy"

    edited_spool = json.loads((spool_dir / "delivery-edited.json").read_text())
    assert edited_spool["status"] == "processed"
    assert edited_spool["prequeue_ignored"] is True
    assert edited_spool["result"]["result"]["reason"] == "unsupported_action"
    assert calls and calls[0]["delivery"] == "delivery-review"
    assert calls[0]["body"] == persisted_body
    assert [call["delivery"] for call in calls] == ["delivery-review"]
    assert calls[0]["force"] is False
    release_worker.set()
    deadline = time.time() + 2
    terminal_record = {}
    while time.time() < deadline:
        terminal_record = json.loads((spool_dir / "delivery-review.json").read_text())
        if terminal_record.get("status") == "processed":
            break
        time.sleep(0.01)
    assert terminal_record["status"] == "processed"

    terminal_duplicate_request = _signed_github_request(
        f"http://127.0.0.1:{port}/webhooks/github",
        duplicate_ignored_body,
        "test-secret",
        event="pull_request",
        delivery="delivery-review",
    )
    with _open_with_retry(terminal_duplicate_request, timeout=5) as terminal_duplicate_response:
        terminal_duplicate_payload = json.loads(terminal_duplicate_response.read())
    preserved_terminal = json.loads((spool_dir / "delivery-review.json").read_text())
    assert terminal_duplicate_response.status == 202
    assert terminal_duplicate_payload["action"] == "duplicate"
    assert terminal_duplicate_payload["status"] == "processed"
    assert preserved_terminal["status"] == "processed"
    assert preserved_terminal["body"] == persisted_body.decode()


def test_cmd_webhook_event_ignores_closed_current_pr(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    payload_path.write_text(json.dumps(_github_pr_webhook_payload(head="abc123")))
    calls = []
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args))
    monkeypatch.setattr(automation, "_fetch_current_pr_for_webhook", lambda repo, number: {
        "number": number,
        "headRefOid": "abc123",
        "isDraft": False,
        "state": "CLOSED",
        "url": "https://github.com/owner/repo/pull/7",
    })
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-closed",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert calls == []
    assert not state.exists()


def test_cmd_webhook_event_ignores_stale_payload_head(monkeypatch, tmp_path: Path):
    config = tmp_path / "repos.json"
    state = tmp_path / "state.json"
    payload_path = tmp_path / "payload.json"
    config.write_text(json.dumps({"repos": {"owner/repo": {"enabled": True}}}))
    state.write_text(json.dumps({"schema_version": 1, "reviews": {"owner/repo#7": {"head_sha": "new456", "success": True}}}))
    payload_path.write_text(json.dumps(_github_pr_webhook_payload(head="old123")))
    calls = []
    monkeypatch.setattr(pr_review_cli, "_run_review", lambda args, ctx=None: calls.append(args))
    monkeypatch.setattr(automation, "_fetch_current_pr_for_webhook", lambda repo, number: {
        "number": number,
        "headRefOid": "new456",
        "isDraft": False,
        "url": "https://github.com/owner/repo/pull/7",
    })
    args = argparse.Namespace(
        pr_review_command="webhook-event",
        config=str(config),
        state=str(state),
        payload=str(payload_path),
        event="pull_request",
        delivery="delivery-stale",
        force=False,
        no_llm=True,
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert calls == []
    saved = json.loads(state.read_text())
    assert saved["reviews"]["owner/repo#7"]["head_sha"] == "new456"
