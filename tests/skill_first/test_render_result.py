"""Presentation contracts; fixture prose is not a real PR assessment."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/hermes-pr-review/scripts"))
from pr_review_lib.workflow import render_result


SCOPE = (
    "Static included-patch and pinned-source assessment only. No code executed, "
    "no tests run, no GitHub publication. Not a merge approval."
)


def record(*, stage="review", coverage="complete", findings=False):
    result = {
        "schema_version": 1, "stage": stage, "coverage": coverage,
        "summary": "Fixture summary: supplied evidence only.",
        "limitations": ["Fixture dependency implementation is missing."] if coverage == "incomplete" else [],
    }
    if stage == "triage":
        result.update(decision="defer" if coverage == "incomplete" else "review",
                      confidence="medium", reason="Fixture routing rationale.")
    else:
        result["findings"] = [{
            "severity": "warning", "title": "Fixture finding", "path": "renamed.py",
            "commit_path": "original.py", "side": "LEFT", "line": 7,
            "commit_sha": "c" * 40, "evidence": "old_guard(value)",
            "why_it_matters": "Fixture consequence remains conditional on the supplied scenario.",
            "suggested_fix": "Fixture correction: restore the guard.",
        }] if findings else []
    return {
        "schema_version": 1, "status": "completed" if coverage == "complete" else "incomplete",
        "ref": "owner/repo#1", "head_sha": "a" * 40, "base_sha": "b" * 40,
        "merge_base_sha": "c" * 40, "model": "test-fixture", "result": result,
    }


@pytest.mark.parametrize("coverage", ["complete", "incomplete"])
def test_review_status_names_scope_and_preserves_outcome(coverage):
    item = record(coverage=coverage)
    report = render_result(item)
    assert f"Static review assessment: {coverage}" in report.split("## Assessment summary")[0]
    assert f"Workflow outcome: {item['status']}" in report
    assert "\nOutcome:" not in report
    assert SCOPE in report
    assert "## Assessment summary\n\n" + item["result"]["summary"] in report


@pytest.mark.parametrize("coverage", ["complete", "incomplete"])
def test_quiet_report_is_explicit_and_never_an_all_clear(coverage):
    report = render_result(record(coverage=coverage))
    findings = report.split("## Findings\n\n", 1)[1].split("\n\n## Coverage and limitations", 1)[0]
    assert findings.strip()
    if coverage == "complete":
        assert findings == "No concrete introduced defects were reported in the supplied evidence."
    else:
        assert findings == "No findings established. Assessment incomplete; missing context prevents a complete review."
        assert "Fixture dependency implementation is missing." in report
        assert "No additional limitations" not in report
    assert "Not a merge approval." in report


def test_positive_report_preserves_every_finding_field_and_readable_sections():
    item = record(findings=True)
    report = render_result(item)
    headings = ["## Assessment summary", "## Findings", "## Coverage and limitations", "## Reviewed evidence identity"]
    assert [report.index(h) for h in headings] == sorted(report.index(h) for h in headings)
    finding = item["result"]["findings"][0]
    assert f"### warning: {finding['title']}" in report
    assert f"original.py:7 (LEFT, {'c' * 40}; diff path renamed.py)" in report
    assert f"Evidence: {finding['evidence']}" in report
    assert "**Why it matters**\n\n" + finding["why_it_matters"] in report
    assert "**Suggested correction / next step**\n\n" + finding["suggested_fix"] in report
    assert "No concrete introduced defects" not in report
    assert "No findings established" not in report
    assert "No additional limitations reported for this scoped assessment." in report
    for key, label in [("ref", "PR"), ("head_sha", "Head"), ("base_sha", "Base"),
                       ("model", "Model (operator-reported)")]:
        assert f"{label}: {item[key]}" in report


def test_incomplete_can_have_findings_without_losing_limitations():
    item = record(coverage="incomplete", findings=True)
    report = render_result(item)
    assert "Static review assessment: incomplete" in report
    assert "### warning: Fixture finding" in report
    assert item["result"]["limitations"][0] in report
    assert "No findings established" not in report


@pytest.mark.parametrize("coverage", ["complete", "incomplete"])
def test_triage_is_not_presented_as_an_implementation_review(coverage):
    item = record(stage="triage", coverage=coverage)
    report = render_result(item)
    assert f"Static triage assessment: {coverage}" in report
    assert "## Triage decision" in report
    assert f"Decision: {item['result']['decision']}" in report
    assert item["result"]["reason"] in report
    assert "Confidence (routing only): medium" in report
    assert "Implementation not reviewed." in report
    assert "No code executed, no tests run, no GitHub publication. Not a merge approval." in report
    assert "## Findings" not in report
    assert "No concrete introduced defects" not in report
    assert "pinned-source assessment" not in report


@pytest.mark.parametrize("status", ["prepared", "collecting", "failed", "stale", "expired", "skipped"])
def test_nonfinal_assessments_cannot_be_rendered_as_complete(status):
    item = record()
    item["status"] = status
    with pytest.raises(ValueError, match="outcome.*coverage"):
        render_result(item)


@pytest.mark.parametrize("status,coverage", [("completed", "incomplete"), ("incomplete", "complete")])
def test_inconsistent_outcome_and_coverage_is_rejected(status, coverage):
    item = record(coverage=coverage)
    item["status"] = status
    with pytest.raises(ValueError, match="outcome.*coverage"):
        render_result(item)


@pytest.mark.parametrize("stage", ["review", "triage"])
@pytest.mark.parametrize("coverage", ["complete", "incomplete"])
def test_rendering_is_deterministic_and_does_not_mutate_judgment(stage, coverage):
    item = record(stage=stage, coverage=coverage, findings=stage == "review")
    before = copy.deepcopy(item)
    assert render_result(item) == render_result(item)
    assert item == before
