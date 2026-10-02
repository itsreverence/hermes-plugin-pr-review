"""Exercise opt-in dependency evidence through the real CLI and private state."""
import hashlib
import json

import pytest

import test_cli

cli = test_cli.cli
judgment = test_cli.judgment


@pytest.mark.parametrize("value", ["0", "-1", "400001", "1.5", "true", "inf"])
def test_dependency_cli_invalid_budget_rejected_before_io(cli, value):
    run, state, log = cli
    code, result = run("prepare", "Org/repo#12", "--stage", "review", "--source-path", "lib/client.py",
                       "--max-dependency-bytes", value)
    assert code == 2 and "must be an integer from 1 to 400000" in result["stderr"]
    assert not state.exists() and not log.exists()


@pytest.mark.parametrize("args", [[], ["--stage", "review"], ["--stage", "triage", "--source-path", "lib/client.py"]])
def test_dependency_cli_requires_review_and_explicit_paths_before_io(cli, args):
    run, state, log = cli
    code, result = run("prepare", "Org/repo#12", "--max-dependency-bytes", "100", *args)
    assert code == 2 and "requires --stage review and --source-path" in result["stderr"]
    assert not state.exists() and not log.exists()


def test_dependency_cli_full_retry_preserves_hold_and_reuses_completed_evidence(cli, tmp_path):
    run, state, log = cli
    args = ("prepare", "Org/repo#12", "--stage", "review", "--source-path", "lib/client.py")
    updates = {"FAKE_GH_DEPENDENCIES": "1"}
    code, held = run(*args, updates=updates)
    assert code == 2 and held["status"] == "incomplete" and "sources_budget" in held["error"]
    original = state / "attempts" / held["id"]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir()}
    code, ready = run(*args, "--max-dependency-bytes", "61898", updates=updates)
    assert code == 0 and ready["status"] == "prepared", ready
    packet_dir = state / "attempts" / ready["id"]
    snapshot = json.loads((packet_dir / "input.json").read_text())["snapshot"]
    assert snapshot["incomplete_reasons"] == []
    assert snapshot["docs"]["lib/AGENTS.md"] == "Dependency guidance from pinned base"
    sources = snapshot["sources"]
    assert len(sources) == 4
    assert {(s["path"], s["side"], s["ref"]) for s in sources} == {
        (path, side, ref) for path in ("src/main.py", "lib/client.py")
        for side, ref in [("RIGHT", "b" * 40), ("LEFT", "d" * 40)]}
    assert all(s["text"] == "d" * 30949 for s in sources if s["path"] == "lib/client.py")
    assert "source_budget" in (packet_dir / "context.md").read_text()
    code, finished = run("finalize", ready["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")
    assert code == 0 and finished["status"] == "completed"
    again = run(*args, "--max-dependency-bytes", "61898", updates=updates)[1]
    assert again["status"] == "skipped" and again["previous_id"] == ready["id"]
    # Different evidence is not allowed to borrow the prior completed judgment.
    narrower = run(*args, updates=updates)[1]
    assert narrower["status"] == "incomplete"
    assert run("status", "--attempt", held["id"])[1]["status"] == "incomplete"
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir()} == before
    assert json.loads((packet_dir / "result.json").read_text())["model"] == "offline-fixture"
    for path in state.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert requests and all(argv[:5] == ["api", "--hostname", "github.com", "--method", "GET"] for argv in requests)


@pytest.mark.parametrize("budget,expected", [("1", "incomplete"), ("399", "incomplete"), ("400", "prepared"), ("400000", "prepared")])
def test_dependency_cli_utf8_and_inclusive_bounds(cli, budget, expected):
    run, state, _ = cli
    code, attempt = run("prepare", "Org/repo#12", "--stage", "review", "--source-path", "lib/client.py",
                        "--max-dependency-bytes", budget,
                        updates={"FAKE_GH_DEPENDENCIES": "1", "FAKE_GH_UNICODE_DEPENDENCY": "1"})
    assert attempt["status"] == expected
    assert code == (0 if expected == "prepared" else 2)
    snapshot = json.loads((state / "attempts" / attempt["id"] / "input.json").read_text())["snapshot"]
    if expected == "incomplete":
        assert "dependency_sources_budget" in snapshot["incomplete_reasons"]
    else:
        assert [s["text"] for s in snapshot["sources"] if s["path"] == "lib/client.py"] == ["é" * 100] * 2


@pytest.mark.parametrize("doc_chars,expected", [(490_000, "incomplete"), (350_000, "prepared")])
def test_dependency_cli_serialized_artifact_limit_still_applies(cli, tmp_path, doc_chars, expected):
    from pr_review_lib.artifacts import read_json, read_text

    run, state, _ = cli
    code, attempt = run("prepare", "Org/repo#12", "--stage", "review", "--source-path", "lib/client.py",
                        "--max-dependency-bytes", "400000", "--max-doc-bytes", "1000000",
                        updates={"FAKE_GH_DEPENDENCIES": "1", "FAKE_GH_UNICODE_DEPENDENCY": "1",
                                 "FAKE_GH_DEPENDENCY_CHARS": "100000", "FAKE_GH_SERIALIZED_DOC_CHARS": str(doc_chars)})
    assert attempt["status"] == expected, attempt
    directory = state / "attempts" / attempt["id"]
    if expected == "incomplete":
        assert code == 2 and attempt["error"] == "artifact_budget"
        diagnostic = read_json(directory / "artifact-budget.json")
        assert max(diagnostic["input_bytes"], diagnostic["context_bytes"]) > diagnostic["limit_bytes"]
        assert not (directory / "input.json").exists() and not (directory / "context.md").exists()
        assert attempt["input_digest"] is None and attempt["snapshot_key"] is None
    else:
        assert code == 0
        assert len(read_json(directory / "input.json")["snapshot"]["sources"]) == 4
        assert read_text(directory / "context.md")
        assert run("finalize", attempt["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")[0] == 0


def test_dependency_cli_flag_not_available_to_finalize(cli):
    run, state, log = cli
    code, result = run("finalize", "a" * 32, "--result", "unused", "--model", "fixture", "--max-dependency-bytes", "100")
    assert code == 2 and "unrecognized arguments" in result["stderr"]
    assert not state.exists() and not log.exists()
