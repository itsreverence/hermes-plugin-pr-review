"""Full CLI subprocess tests with a fake gh executable, never live network."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "skills/hermes-pr-review/scripts/pr_review.py"
sys.path.insert(0, str(HELPER.parent))
from pr_review_lib.artifacts import read_json, read_text, write_json  # noqa: E402


@pytest.fixture
def cli(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(f'''#!{sys.executable}
import json, os, runpy, sys
from pathlib import Path
fixture = runpy.run_path({str(Path(__file__).with_name("test_github.py"))!r})
fake = fixture["FakeGitHub"]()
if os.environ.get("FAKE_GH_LARGE_DOCS"):
    # Synthetic documents with the observed upstream UTF-8 byte sizes.
    fake.docs = {{"AGENTS.md": "a" * 31874, "README.md": "r" * 17688,
                  "CONTRIBUTING.md": "c" * 51333}}
if os.environ.get("FAKE_GH_UNICODE_DOC"):
    fake.docs = {{"README.md": "é" * 10}}
if os.environ.get("FAKE_GH_SERIALIZED_DOC_CHARS"):
    fake.docs = {{"README.md": "é" * int(os.environ["FAKE_GH_SERIALIZED_DOC_CHARS"])}}
    fake.sources = {{(fixture["HEAD"], "src/main.py"): "é" * 100000,
                    (fixture["MERGE_BASE"], "src/main.py"): "é" * 100000}}
if os.environ.get("FAKE_GH_STALE"):
    fake.meta["head"]["sha"] = "c" * 40
if os.environ.get("FAKE_GH_BINARY"):
    fake.files[0]["patch"] = None
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
result = fake(["gh", *sys.argv[1:]], timeout=20)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
''')
    gh.chmod(0o700)
    state = tmp_path / "state"
    log = tmp_path / "gh-argv.jsonl"
    env = os.environ | {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "FAKE_GH_LOG": str(log)}
    def run(*args, updates=None):
        proc = subprocess.run([sys.executable, str(HELPER), "--state-root", str(state), *args], cwd=tmp_path,
                              env=env | (updates or {}), capture_output=True, text=True, timeout=30)
        return proc.returncode, json.loads(proc.stdout) if proc.stdout else {"stderr": proc.stderr}
    return run, state, log


def judgment(tmp_path, *, stage="review"):
    value = {"schema_version": 1, "stage": stage, "coverage": "complete", "summary": "Offline CLI fixture, not a real review.", "limitations": []}
    value.update({"findings": []} if stage == "review" else {"decision": "review", "confidence": "high", "reason": "Fixture triage."})
    path = tmp_path / "judgment.json"
    path.write_text(json.dumps(value))
    return str(path)


def test_end_to_end_cli_dedupe_rerun_and_private_readback(cli, tmp_path):
    run, state, log = cli
    code, first = run("prepare", "https://github.com/Org/repo/pull/12", "--stage", "review")
    assert code == 0 and first["status"] == "prepared"
    code, completed = run("finalize", first["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")
    assert code == 0 and completed["status"] == "completed"
    assert run("status", "--attempt", first["id"])[1]["status"] == "completed"
    assert run("prepare", "Org/repo#12", "--stage", "review")[1]["status"] == "skipped"
    rerun = run("prepare", "Org/repo#12", "--stage", "review", "--rerun-reason", "explicit test rerun")[1]
    assert rerun["status"] == "prepared" and rerun["id"] != first["id"]
    assert run("fail", rerun["id"], "--reason", "model_timeout")[1]["status"] == "failed"
    report = json.loads((state / "attempts" / first["id"] / "result.json").read_text())
    assert report["model"] == "offline-fixture"
    for path in state.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    requests = [json.loads(line) for line in log.read_text().splitlines()]
    assert requests and all(argv[:5] == ["api", "--hostname", "github.com", "--method", "GET"] for argv in requests)


def test_cli_stale_finalization_and_triage_binary_scope(cli, tmp_path):
    run, state, _ = cli
    attempt = run("prepare", "Org/repo#12", "--stage", "review")[1]
    code, stale = run("finalize", attempt["id"], "--result", judgment(tmp_path), "--model", "offline-fixture", updates={"FAKE_GH_STALE": "1"})
    assert code == 2 and stale["status"] == "stale"
    assert not (state / "attempts" / attempt["id"] / "result.json").exists()
    code, triage = run("prepare", "Org/repo#12", "--stage", "triage", updates={"FAKE_GH_BINARY": "1"})
    assert code == 0 and triage["status"] == "prepared"
    packet = json.loads((state / "attempts" / triage["id"] / "input.json").read_text())
    assert packet["snapshot"]["files"][0]["patch"] is None
    assert run("finalize", triage["id"], "--result", judgment(tmp_path, stage="triage"), "--model", "offline-fixture")[0] == 0
    code, held = run("prepare", "Org/repo#12", "--stage", "review", updates={"FAKE_GH_BINARY": "1"})
    assert code == 2 and held["status"] == "incomplete"


def test_cli_explicit_document_budget_collects_full_docs_without_mutating_hold(cli, tmp_path):
    run, state, _ = cli
    updates = {"FAKE_GH_LARGE_DOCS": "1"}
    code, held = run("prepare", "Org/repo#12", "--stage", "review", updates=updates)
    assert code == 2 and held["status"] == "incomplete" and held["error"] == "docs_budget"
    original = state / "attempts" / held["id"] / "input.json"
    retained = original.read_bytes()
    code, ready = run("prepare", "Org/repo#12", "--stage", "review",
                      "--max-doc-bytes", "200000", updates=updates)
    assert code == 0 and ready.get("status") == "prepared", ready
    packet = json.loads((state / "attempts" / ready["id"] / "input.json").read_text())
    docs = packet["snapshot"]["docs"]
    assert docs == {"AGENTS.md": "a" * 31874, "README.md": "r" * 17688,
                    "CONTRIBUTING.md": "c" * 51333}
    assert not packet["snapshot"]["incomplete_reasons"]
    assert original.read_bytes() == retained
    assert run("status", "--attempt", held["id"])[1]["status"] == "incomplete"
    assert run("finalize", ready["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")[0] == 0
    repeated = run("prepare", "Org/repo#12", "--stage", "review", "--max-doc-bytes", "200000", updates=updates)[1]
    assert repeated["status"] == "skipped" and repeated["previous_id"] == ready["id"]
    # A narrower subsequent collection must not borrow completed coverage.
    assert run("prepare", "Org/repo#12", "--stage", "review", updates=updates)[1]["status"] == "incomplete"


@pytest.mark.parametrize("value", ["0", "-1", "1000001", "1.5", "true", "inf"])
def test_cli_rejects_out_of_range_doc_budget_before_io(cli, value):
    run, state, log = cli
    code, result = run("prepare", "Org/repo#12", "--max-doc-bytes", value)
    assert code == 2 and "must be an integer from 1 to 1000000" in result["stderr"]
    assert not state.exists() and not log.exists()


@pytest.mark.parametrize("budget,expected", [("19", "incomplete"), ("20", "prepared")])
def test_cli_doc_budget_measures_utf8_bytes_not_characters(cli, budget, expected):
    run, state, _ = cli
    code, result = run("prepare", "Org/repo#12", "--stage", "review", "--max-doc-bytes", budget,
                       updates={"FAKE_GH_UNICODE_DOC": "1"})
    assert result["status"] == expected
    assert code == (0 if expected == "prepared" else 2)
    packet = json.loads((state / "attempts" / result["id"] / "input.json").read_text())
    assert packet["snapshot"]["docs"] == ({"README.md": "é" * 10} if expected == "prepared" else {})


@pytest.mark.parametrize("value", ["1", "1000000"])
def test_cli_doc_budget_boundaries_remain_bounded(cli, value):
    run, state, _ = cli
    code, result = run("prepare", "Org/repo#12", "--stage", "review", "--max-doc-bytes", value)
    if value == "1":
        assert code == 2 and result["status"] == "incomplete" and result["error"] == "docs_budget"
    else:
        assert code == 0 and result["status"] == "prepared"
        packet = json.loads((state / "attempts" / result["id"] / "input.json").read_text())
        assert packet["snapshot"]["docs"] == {"README.md": "Trusted base documentation"}


@pytest.mark.parametrize("doc_chars", [500_000, 450_000])
def test_cli_serialized_unicode_artifact_admission(cli, tmp_path, doc_chars):
    run, state, log = cli
    # A new collection must neither repair nor discard an existing failure.
    prior = run("prepare", "Org/repo#12", "--stage", "review")[1]
    assert run("fail", prior["id"], "--reason", "operator_cancelled")[0] == 2
    prior_dir = state / "attempts" / prior["id"]
    retained = {p.name: p.read_bytes() for p in prior_dir.iterdir()}
    code, attempt = run("prepare", "Org/repo#12", "--stage", "review",
                        "--max-doc-bytes", "1000000",
                        updates={"FAKE_GH_SERIALIZED_DOC_CHARS": str(doc_chars)})
    directory = state / "attempts" / attempt["id"]
    if doc_chars == 500_000:
        # Explain the historical failure with actual reader/finalizer output.
        if attempt["status"] == "prepared":
            size = (directory / "input.json").stat().st_size
            with pytest.raises(ValueError, match="oversized artifact"):
                read_json(directory / "input.json")
            old = run("finalize", attempt["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")[1]
            pytest.fail(f"prepared unreadable input: {size} bytes; finalize={old['status']}/{old['error']}")
        assert code == 2 and attempt["status"] == "incomplete" and attempt["error"] == "artifact_budget"
        assert attempt["input_digest"] is None and attempt["snapshot_key"] is None
        diagnostic_path = directory / "artifact-budget.json"
        diagnostic = read_json(diagnostic_path)
        assert diagnostic == {
            "schema_version": 1, "attempt_id": attempt["id"], "ref": "Org/repo#12", "stage": "review",
            "head_sha": "b" * 40, "base_sha": "a" * 40, "merge_base_sha": "d" * 40,
            "status": "incomplete", "error": "artifact_budget", "limit_bytes": 4_000_000,
            "input_bytes": diagnostic["input_bytes"], "context_bytes": diagnostic["context_bytes"],
        }
        assert diagnostic["input_bytes"] > diagnostic["limit_bytes"]
        assert diagnostic["context_bytes"] > diagnostic["limit_bytes"]
        assert diagnostic_path.stat().st_size < 2_000
        assert diagnostic_path.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700
        saved = diagnostic_path.read_bytes()
        with pytest.raises(ValueError, match="immutable artifact"):
            write_json(diagnostic_path, diagnostic)
        requests = log.read_bytes()
        for args in [
            ("finalize", attempt["id"], "--result", judgment(tmp_path), "--model", "offline-fixture"),
            ("judge", attempt["id"], "--provider", "openai-codex", "--model", "offline-fixture"),
        ]:
            denied_code, denied = run(*args)
            assert denied_code == 1 and denied["error"] == "StateError"
        assert log.read_bytes() == requests  # No final snapshot/provider admission.
        assert run("status", "--attempt", attempt["id"])[1] == attempt
        assert diagnostic_path.read_bytes() == saved
        assert {p.name for p in directory.iterdir()} == {"artifact-budget.json"}
    else:
        assert code == 0 and attempt["status"] == "prepared", attempt
        packet = read_json(directory / "input.json")
        assert packet["snapshot"]["docs"] == {"README.md": "é" * doc_chars}
        assert [s["text"] for s in packet["snapshot"]["sources"]] == ["é" * 100_000] * 2
        assert packet["snapshot"]["incomplete_reasons"] == []
        assert 3_900_000 < (directory / "input.json").stat().st_size < 4_000_000
        assert read_text(directory / "context.md")
        code, completed = run("finalize", attempt["id"], "--result", judgment(tmp_path), "--model", "offline-fixture")
        assert code == 0 and completed["status"] == "completed"
        assert read_json(directory / "result.json")["status"] == "completed"
        assert run("status", "--attempt", attempt["id"])[1]["status"] == "completed"
    assert {p.name: p.read_bytes() for p in prior_dir.iterdir()} == retained
    assert run("status", "--attempt", prior["id"])[1]["status"] == "failed"
