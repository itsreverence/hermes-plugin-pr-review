"""Full CLI subprocess tests with a fake gh executable, never live network."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "skills/hermes-pr-review/scripts/pr_review.py"


@pytest.fixture
def cli(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(f'''#!{sys.executable}
import json, os, runpy, sys
from pathlib import Path
fixture = runpy.run_path({str(Path(__file__).with_name("test_github.py"))!r})
fake = fixture["FakeGitHub"]()
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
        return proc.returncode, json.loads(proc.stdout or proc.stderr)
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
