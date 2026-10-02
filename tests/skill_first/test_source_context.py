"""Source-enriched workflow contracts, without GitHub or model calls."""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib.state import State
from pr_review_lib.workflow import context_text, finalize, prepare


class Collector:
    def collect(self, ref, *, stage="review", source_paths=()):
        self.requested = source_paths
        return {
            "repo": "owner/repo", "number": 1, "head_sha": "a" * 40,
            "base_sha": "b" * 40, "merge_base_sha": "c" * 40,
            "state": "open", "draft": False, "title": "Fixture", "body": "",
            "docs": {}, "policy": {}, "incomplete_reasons": [],
            "files": [{"filename": "change.py", "patch": "@@ -1 +1 @@\n-old\n+new"}],
            "sources": [{"path": path, "ref": "a" * 40, "side": "RIGHT", "text": "dependency\n"} for path in source_paths],
        }

    def metadata(self, ref):
        return self.collect(ref)


def test_explicit_context_reaches_collector_and_changes_dedupe(tmp_path):
    state, github = State(tmp_path / "state"), Collector()
    one = prepare(state, github, "owner/repo#1", "review")
    path = tmp_path / "judgment.json"
    path.write_text(json.dumps({"schema_version": 1, "stage": "review", "coverage": "complete", "summary": "Fixture only", "limitations": [], "findings": []}))
    assert finalize(state, github, one["id"], path, model="fixture")["status"] == "completed"
    two = prepare(state, github, "owner/repo#1", "review", source_paths=("lib/dependency.py",))
    assert github.requested == ("lib/dependency.py",)
    assert two["status"] == "prepared", "additional evidence must not reuse a narrower review"
    packet = json.loads((state.root / "attempts" / two["id"] / "input.json").read_text())
    assert packet["snapshot"]["sources"][0]["path"] == "lib/dependency.py"


def test_sources_render_with_exact_commit_and_bounded_line_records():
    snapshot = Collector().collect("owner/repo#1", source_paths=("lib/dependency.py",))
    snapshot["sources"][0]["text"] = "first\nsecond\u2028not-a-new-Git-line\n"
    original = copy.deepcopy(snapshot)
    text = context_text({"stage": "review", "snapshot": snapshot})
    assert "## Surrounding source" in text
    assert '"ref": "' + "a" * 40 + '"' in text
    assert '1: "first"' in text
    assert '2: "second\\u2028not-a-new-Git-line"' in text
    assert snapshot == original
