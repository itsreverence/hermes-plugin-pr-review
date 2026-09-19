"""Real SQLite and filesystem tests; no GitHub/model calls."""
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib.state import State, StateError


def claim_worker(root, barrier, queue):
    state = State(Path(root))
    barrier.wait()
    try:
        queue.put(state.start("owner/repo#1", "review")["status"])
    except StateError:
        queue.put("busy")


def test_concurrent_process_claim(tmp_path):
    root = tmp_path / "private"
    State(root)
    ctx = multiprocessing.get_context("spawn")
    barrier, queue = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=claim_worker, args=(str(root), barrier, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    assert sorted([queue.get(timeout=2), queue.get(timeout=2)]) == ["busy", "collecting"]


def test_dedupe_stage_and_intentional_rerun(tmp_path):
    state = State(tmp_path / "private")
    first = state.start("owner/repo#1", "review")
    assert state.prepared(first["id"], "key", "digest")["status"] == "prepared"
    state.finish(first["id"], "completed")
    duplicate = state.start("owner/repo#1", "review")
    duplicate = state.prepared(duplicate["id"], "key", "digest")
    assert duplicate["status"] == "skipped"
    assert duplicate["previous_id"] == first["id"]
    triage = state.start("owner/repo#1", "triage")
    assert state.prepared(triage["id"], "key", "digest")["status"] == "prepared"
    rerun = state.start("owner/repo#1", "review", rerun_reason="second opinion")
    assert state.prepared(rerun["id"], "key", "digest")["status"] == "prepared"
    assert first["id"] != rerun["id"]


def test_expiry_fences_old_writer_and_preserves_attempt(tmp_path):
    clock = [1000.0]
    state = State(tmp_path / "private", clock=lambda: clock[0], lease_seconds=10)
    old = state.start("owner/repo#1", "review")
    state.prepared(old["id"], "a", "d")
    clock[0] += 11
    new = state.start("owner/repo#1", "review")
    with pytest.raises(StateError, match="active|expired"):
        state.finish(old["id"], "completed")
    assert state.get(old["id"])["status"] == "expired"
    assert state.get(new["id"])["status"] == "collecting"


def test_failure_is_retained_and_retry_not_suppressed(tmp_path):
    state = State(tmp_path / "private")
    first = state.start("owner/repo#1", "review")
    state.finish(first["id"], "failed", error="collection_failed")
    second = state.start("owner/repo#1", "review")
    assert second["id"] != first["id"]
    assert state.get(first["id"])["error"] == "collection_failed"


def test_terminal_attempt_cannot_be_finalized_twice(tmp_path):
    state = State(tmp_path / "private")
    attempt = state.start("owner/repo#1", "review")
    state.prepared(attempt["id"], "a", "d")
    state.finish(attempt["id"], "completed")
    with pytest.raises(StateError):
        state.finish(attempt["id"], "failed")


def test_private_permissions_symlink_and_corrupt_db(tmp_path):
    root = tmp_path / "private"
    State(root)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "state.sqlite3").stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises((StateError, ValueError)):
        State(link)
    (root / "state.sqlite3").write_bytes(b"broken database")
    with pytest.raises(StateError):
        State(root)
    assert (root / "state.sqlite3").read_bytes() == b"broken database"


def test_permissive_existing_state_root_rejected(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    os.chmod(root, 0o755)
    with pytest.raises((StateError, ValueError)):
        State(root)
