from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from plugins.pr_review import cli as pr_review_cli
from plugins.pr_review import dogfood


def test_copy_dogfood_artifacts_stays_owner_only(tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("private review", encoding="utf-8")
    payload = {"paths": {"review": str(source)}}
    output_dir = tmp_path / "runs"

    dogfood._copy_dogfood_artifacts(payload, output_dir=output_dir, run_id="run-1", case_id="case", variant="baseline")

    artifact_root = output_dir / "run-1-artifacts"
    copied = Path(payload["paths"]["review"])
    assert artifact_root.stat().st_mode & 0o777 == 0o700
    assert copied.stat().st_mode & 0o777 == 0o600
    assert copied.read_text(encoding="utf-8") == "private review"


def test_copy_dogfood_artifacts_rejects_symlinked_output(tmp_path: Path):
    source = tmp_path / "source.md"
    source.write_text("private review", encoding="utf-8")
    payload = {"paths": {"review": str(source)}}
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (output_dir / "run-1-artifacts").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        dogfood._copy_dogfood_artifacts(payload, output_dir=output_dir, run_id="run-1", case_id="case", variant="baseline")

    assert list(target.iterdir()) == []


def test_write_dogfood_summary_rejects_symlinked_observation_lock(monkeypatch, tmp_path: Path):
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    victim = tmp_path / "outside.lock"
    victim.write_text("unchanged", encoding="utf-8")
    (output_dir / "observations.lock").symlink_to(victim)
    monkeypatch.setattr(dogfood, "_render_dogfood_markdown", lambda _summary: "summary\n")
    monkeypatch.setattr(dogfood, "_observation_record", lambda _summary: {})

    with pytest.raises(ValueError, match="observation lock must not be a symlink"):
        dogfood._write_dogfood_summary({"run_id": "run-1"}, output_dir)

    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_observation_lock_swap_cannot_chmod_or_write_victim(monkeypatch, tmp_path: Path):
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    lock_path = output_dir / "observations.lock"
    lock_path.write_text("", encoding="utf-8")
    victim = tmp_path / "victim"
    victim.write_text("do not touch", encoding="utf-8")
    victim.chmod(0o644)
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777):
        nonlocal swapped
        fd = real_open(path, flags, mode)
        if Path(path) == lock_path and not swapped:
            swapped = True
            lock_path.unlink()
            lock_path.symlink_to(victim)
        return fd

    monkeypatch.setattr(dogfood.os, "open", swapping_open)
    monkeypatch.setattr(dogfood, "_observation_record", lambda _summary: {})
    dogfood._write_observation_history({"run_id": "run-1"}, output_dir)

    assert victim.read_text(encoding="utf-8") == "do not touch"
    assert victim.stat().st_mode & 0o777 == 0o644


def test_observation_lock_closes_fd_when_fchmod_fails(monkeypatch, tmp_path: Path):
    output_dir = tmp_path / "runs"
    closed = []
    real_close = os.close
    monkeypatch.setattr(dogfood.core, "_fchmod", lambda _fd, _mode: (_ for _ in ()).throw(OSError("chmod failed")))
    monkeypatch.setattr(dogfood.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])

    with pytest.raises(OSError, match="chmod failed"):
        dogfood._write_observation_history({"run_id": "run-1"}, output_dir)

    assert len(closed) == 1


def test_cmd_dogfood_run_writes_no_post_summary(monkeypatch, tmp_path: Path):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "observed_at": "2026-06-24T00:00:00Z",
        "cases": [
            {
                "id": "clean",
                "pr": "owner/repo#1",
                "category": "small-docs",
                "title": "Clean docs",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
                "expectations": {
                    "expected_findings_max": 0,
                    "expected_risk": "low",
                    "expected_truncated": False,
                    "expected_docs_loaded_min": 1,
                    "expected_posted_comments": 0,
                },
            },
            {
                "id": "risky",
                "pr": "owner/repo#2",
                "category": "backend",
                "title": "Risky backend",
                "observed_head_sha": "def",
                "observed_check_status": {"failure": 1},
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))
    calls = []

    def fake_run_review(args, ctx=None):
        calls.append(args)
        assert args.post_comment is False
        assert args.allow_truncated_post is False
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": int(args.pr.rsplit("#", 1)[1]),
            "pr_ref": args.pr,
            "head_sha": "abc",
            "verdict": "comment",
            "model_verdict": "comment",
            "risk": "low",
            "mode": args.mode,
            "findings": 0,
            "paths": {"review": str(tmp_path / f"review-{args.pr[-1]}.md")},
            "docs_loaded": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    args = argparse.Namespace(
        pr_review_command="dogfood-run",
        manifest=str(manifest_path),
        case_ids=["clean"],
        limit=None,
        no_llm=True,
        max_diff_chars=5000,
        mode="balanced",
        output_dir=str(tmp_path / "runs"),
        run_id="unit-run",
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.pr for call in calls] == ["owner/repo#1"]
    summary = json.loads((tmp_path / "runs" / "unit-run.json").read_text())
    markdown = (tmp_path / "runs" / "unit-run.md").read_text()
    assert summary["case_count"] == 1
    assert summary["posted_comment_count"] == 0
    assert summary["expectation_failure_count"] == 0
    assert summary["paths"]["markdown"].endswith("unit-run.md")
    assert summary["manifest"]["identity"]
    assert summary["manifest"]["sha256"]
    assert summary["paths"]["observations"].endswith("observations.jsonl")
    assert summary["paths"]["observations_summary"].endswith("observations-summary.json")
    assert summary["cases"][0]["case_id"] == "clean"
    assert summary["cases"][0]["expectation"]["passed"] is True
    assert summary["manual_scoring"]["schema_version"] == 1
    assert any(bucket["key"] == "graph_missed_useful_baseline" for bucket in summary["manual_scoring"]["manual_score_buckets"])
    assert any(check["key"] == "safe_to_post" for check in summary["manual_scoring"]["posting_quality_checks"])
    assert "Expected | Findings" in markdown
    assert "Manual scoring guide" in markdown
    assert "graph_missed_useful_baseline" in markdown
    assert "Manual scoring:" in markdown
    assert "Observation history:" in markdown
    assert "Expectations: pass" in markdown
    assert "Would post publicly? no" in markdown
    assert "TODO: fill after manual inspection" in markdown


def test_cmd_dogfood_run_compares_baseline_and_graph_variants(monkeypatch, tmp_path: Path):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "cases": [
            {
                "id": "clean",
                "pr": "owner/repo#1",
                "category": "backend",
                "title": "Clean",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))
    graph_repo = tmp_path / "graph-repo"
    graph_repo.mkdir()
    graph_map = tmp_path / "graph-map.json"
    graph_map.write_text(json.dumps({"clean": str(graph_repo)}))
    calls = []

    def fake_run_review(args, ctx=None):
        calls.append(args)
        is_graph = bool(args.graph_context)
        assert args.post_comment is False
        assert args.allow_truncated_post is False
        if is_graph:
            assert args.local_repo == str(graph_repo)
            assert args.graph_context_binary == "codegraph"
        review_path = tmp_path / ("graph.md" if is_graph else "baseline.md")
        review_path.write_text("graph" if is_graph else "baseline")
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 1,
            "pr_ref": args.pr,
            "head_sha": "abc",
            "verdict": "comment",
            "model_verdict": "comment",
            "risk": "low" if not is_graph else "medium",
            "mode": args.mode,
            "findings": 0 if not is_graph else 1,
            "paths": {"review": str(review_path)},
            "docs_loaded": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
            "context_fingerprint": "ctx-graph" if is_graph else "ctx-base",
            "review_fingerprint": "rev-graph" if is_graph else "rev-base",
            "graph_context": {"enabled": True, "status": "collected"} if is_graph else {"enabled": False},
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    args = argparse.Namespace(
        pr_review_command="dogfood-run",
        manifest=str(manifest_path),
        case_ids=[],
        limit=None,
        no_llm=True,
        max_diff_chars=5000,
        mode="balanced",
        output_dir=str(tmp_path / "runs"),
        run_id="ab-run",
        json=True,
        variants=["baseline", "graph"],
        graph_local_repo_map=str(graph_map),
        graph_context_binary="codegraph",
        graph_index_mode="fast",
        max_graph_context_chars=2000,
    )

    rc = pr_review_cli.pr_review_command(args)

    assert rc == 0
    assert [call.graph_context for call in calls] == [False, True]
    summary = json.loads((tmp_path / "runs" / "ab-run.json").read_text())
    markdown = (tmp_path / "runs" / "ab-run.md").read_text()
    assert summary["variants"] == ["baseline", "graph"]
    assert summary["case_count"] == 2
    comparison = summary["variant_comparisons"][0]
    assert isinstance(comparison.pop("baseline_elapsed_sec"), (int, float))
    assert isinstance(comparison.pop("graph_elapsed_sec"), (int, float))
    assert isinstance(comparison.pop("elapsed_delta_sec"), (int, float))
    assert comparison == {
        "case_id": "clean",
        "pr_ref": "owner/repo#1",
        "baseline_success": True,
        "graph_success": True,
        "baseline_findings": 0,
        "graph_findings": 1,
        "finding_delta": 1,
        "baseline_risk": "low",
        "graph_risk": "medium",
        "baseline_review": str(tmp_path / "runs" / "ab-run-artifacts" / "clean__baseline.review.md"),
        "graph_review": str(tmp_path / "runs" / "ab-run-artifacts" / "clean__graph.review.md"),
        "graph_context": {"enabled": True, "status": "collected"},
    }
    assert "Variant comparison" in markdown
    assert "`baseline`" in markdown
    assert "`graph`" in markdown


def test_cmd_dogfood_run_fails_when_expectations_do_not_match(monkeypatch, tmp_path: Path):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "cases": [
            {
                "id": "strict-clean",
                "pr": "owner/repo#1",
                "category": "small-docs",
                "title": "Strict clean",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
                "expectations": {"expected_findings_max": 0, "expected_posted_comments": 0},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))

    def fake_run_review(args, ctx=None):
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 1,
            "pr_ref": args.pr,
            "head_sha": "abc",
            "verdict": "comment",
            "model_verdict": "comment",
            "risk": "medium",
            "mode": args.mode,
            "findings": 1,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    args = argparse.Namespace(
        pr_review_command="dogfood-run",
        manifest=str(manifest_path),
        case_ids=[],
        limit=None,
        no_llm=True,
        max_diff_chars=5000,
        mode="balanced",
        output_dir=str(tmp_path / "runs"),
        run_id="unit-run",
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    summary = json.loads((tmp_path / "runs" / "unit-run.json").read_text())
    markdown = (tmp_path / "runs" / "unit-run.md").read_text()
    assert rc == 1
    assert summary["success"] is False
    assert summary["expectation_failure_count"] == 1
    assert "findings 1 > expected max 0" in summary["expectation_failures"]
    assert "Expectations: FAIL" in markdown


def test_cmd_dogfood_run_observation_history_tracks_variability(monkeypatch, tmp_path: Path):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "cases": [
            {
                "id": "flaky",
                "pr": "owner/repo#1",
                "category": "backend",
                "title": "Flaky case",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
                "expectations": {"expected_findings_max": 2, "expected_posted_comments": 0},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))
    findings_values = iter([0, 2])

    def fake_run_review(args, ctx=None):
        findings = next(findings_values)
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 1,
            "pr_ref": args.pr,
            "head_sha": "abc",
            "verdict": "comment",
            "model_verdict": "comment",
            "risk": "low" if findings == 0 else "medium",
            "mode": args.mode,
            "findings": findings,
            "paths": {"review": str(tmp_path / f"review-{findings}.md")},
            "docs_loaded": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    for run_id in ("first", "second"):
        args = argparse.Namespace(
            pr_review_command="dogfood-run",
            manifest=str(manifest_path),
            case_ids=[],
            limit=None,
            no_llm=False,
            max_diff_chars=5000,
            mode="balanced",
            output_dir=str(tmp_path / "runs"),
            run_id=run_id,
            json=True,
        )
        assert pr_review_cli.pr_review_command(args) == 0

    history_lines = (tmp_path / "runs" / "observations.jsonl").read_text().splitlines()
    observation_summary = json.loads((tmp_path / "runs" / "observations-summary.json").read_text())
    case_summary = observation_summary["cases"][0]
    assert len(history_lines) == 2
    assert observation_summary["run_count"] == 2
    assert case_summary["case_id"] == "flaky"
    assert case_summary["findings_min"] == 0
    assert case_summary["findings_max"] == 2
    assert case_summary["findings_values"] == [0, 2]
    assert case_summary["stable_findings"] is False
    assert case_summary["risk_values"] == ["low", "medium"]


def test_observation_summary_separates_manifests_for_same_case_id():
    records = [
        {
            "manifest": "same-name",
            "manifest_identity": "manifest-a",
            "cases": [{"case_id": "shared", "pr_ref": "owner/a#1", "success": True, "findings": 0, "risk": "low", "diff_truncated": False}],
        },
        {
            "manifest": "same-name",
            "manifest_identity": "manifest-b",
            "cases": [{"case_id": "shared", "pr_ref": "owner/b#2", "success": True, "findings": 2, "risk": "medium", "diff_truncated": False}],
        },
    ]

    summary = dogfood._summarize_observations(records)

    assert summary["case_count"] == 2
    identities = {case["manifest_identity"] for case in summary["cases"]}
    assert identities == {"manifest-a", "manifest-b"}
    assert sorted(case["findings_max"] for case in summary["cases"]) == [0, 2]


def test_cmd_dogfood_score_appends_manual_record_and_report(tmp_path: Path, capsys):
    run_path = tmp_path / "run.json"
    score_path = tmp_path / "scores.jsonl"
    run_path.write_text(
        json.dumps(
            {
                "run_id": "ab",
                "manifest": {"name": "mini"},
                "cases": [],
                "variant_comparisons": [
                    {
                        "case_id": "case-a",
                        "pr_ref": "owner/repo#1",
                        "baseline_findings": 1,
                        "graph_findings": 1,
                        "finding_delta": 0,
                        "baseline_risk": "medium",
                        "graph_risk": "medium",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    score_args = argparse.Namespace(
        pr_review_command="dogfood-score",
        run_json=str(run_path),
        case_id="case-a",
        score_file=str(score_path),
        buckets=["same_useful_finding", "graph_sharper"],
        quality="post_worthy",
        safe_to_post="yes",
        default_vote="graph_better",
        notes="Graph gave better evidence.",
        json=True,
    )

    assert pr_review_cli.pr_review_command(score_args) == 0
    record = json.loads(score_path.read_text(encoding="utf-8"))
    assert record["case_id"] == "case-a"
    assert record["default_vote"] == "graph_better"
    assert record["buckets"] == ["same_useful_finding", "graph_sharper"]
    assert record["quality"] == "post_worthy"
    assert record["comparison"]["pr_ref"] == "owner/repo#1"
    capsys.readouterr()

    report_args = argparse.Namespace(pr_review_command="dogfood-report", score_file=str(score_path), json=True)
    assert pr_review_cli.pr_review_command(report_args) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["success"] is True
    assert report["scored_records"] == 1
    assert report["default_vote_counts"]["graph_better"] == 1
    assert report["bucket_counts"]["graph_sharper"] == 1
    assert report["quality_counts"]["post_worthy"] == 1
    assert report["posting_recommendation"] == "insufficient_data"


def test_score_report_blocks_truncated_public_posting_recommendation():
    records = []
    for index in range(5):
        quality = "post_worthy" if index == 0 else "artifact_only"
        records.append(
            {
                "case_id": f"case-{index}",
                "quality": quality,
                "safe_to_post": "yes" if index == 0 else "n/a",
                "default_vote": "inconclusive",
                "score_context": {
                    "score_context": "single_variant",
                    "diff_truncated": index == 0,
                },
            }
        )

    report = dogfood._score_report(records)

    assert report["graph_scored_records"] == 0
    assert report["graph_recommendation"] == "insufficient_data"
    assert report["truncated_public_posting_records"] == 1
    assert report["posting_recommendation"] == "hold_posting_collect_more"


def test_score_report_blocks_unsafe_to_post_recommendation():
    records = []
    for index in range(5):
        records.append(
            {
                "case_id": f"case-{index}",
                "quality": "post_worthy" if index == 0 else "artifact_only",
                "safe_to_post": "no" if index == 1 else ("yes" if index == 0 else "n/a"),
                "default_vote": "inconclusive",
                "score_context": {
                    "score_context": "single_variant",
                    "diff_truncated": False,
                },
            }
        )

    report = dogfood._score_report(records)

    assert report["unsafe_to_post_records"] == 1
    assert report["posting_recommendation"] == "hold_posting_collect_more"


def test_cmd_dogfood_score_accepts_single_variant_run(tmp_path: Path, capsys):
    run_path = tmp_path / "run.json"
    score_path = tmp_path / "scores.jsonl"
    run_path.write_text(
        json.dumps(
            {
                "run_id": "single",
                "manifest": {"name": "mini"},
                "variant_comparisons": [],
                "cases": [
                    {
                        "case_id": "case-a",
                        "variant": "baseline",
                        "success": True,
                        "pr_ref": "owner/repo#2",
                        "findings": 0,
                        "risk": "low",
                        "diff_truncated": False,
                        "docs_loaded": ["README.md"],
                        "skipped_files": [],
                        "check_context": {"counts": {"success": 1}},
                        "paths": {"review": "runs/case-a.review.md"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        pr_review_command="dogfood-score",
        run_json=str(run_path),
        case_id="case-a",
        score_file=str(score_path),
        buckets=[],
        quality="artifact_only",
        safe_to_post="n/a",
        default_vote="inconclusive",
        notes="quiet control case",
        json=True,
    )

    assert pr_review_cli.pr_review_command(args) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    record = payload["record"]
    assert record["comparison"] is None
    assert record["score_context"]["score_context"] == "single_variant"
    assert record["score_context"]["pr_ref"] == "owner/repo#2"
    assert record["quality"] == "artifact_only"


def test_cmd_dogfood_score_requires_quality_for_single_variant(tmp_path: Path, capsys):
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case-a",
                        "variant": "baseline",
                        "success": True,
                        "pr_ref": "owner/repo#2",
                    }
                ],
                "variant_comparisons": [],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        pr_review_command="dogfood-score",
        run_json=str(run_path),
        case_id="case-a",
        score_file=str(tmp_path / "scores.jsonl"),
        buckets=[],
        quality=None,
        safe_to_post="n/a",
        default_vote=None,
        notes="",
        json=True,
    )

    assert pr_review_cli.pr_review_command(args) == 1
    captured = capsys.readouterr()
    assert "--quality is required" in captured.out


def test_cmd_dogfood_score_requires_default_vote_for_paired_run(tmp_path: Path, capsys):
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "cases": [],
                "variant_comparisons": [
                    {"case_id": "case-a", "pr_ref": "owner/repo#1", "baseline_findings": 0, "graph_findings": 0}
                ],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        pr_review_command="dogfood-score",
        run_json=str(run_path),
        case_id="case-a",
        score_file=str(tmp_path / "scores.jsonl"),
        buckets=[],
        quality="artifact_only",
        safe_to_post="n/a",
        default_vote=None,
        notes="",
        json=True,
    )

    assert pr_review_cli.pr_review_command(args) == 1
    captured = capsys.readouterr()
    assert "--default-vote is required" in captured.out


def test_cmd_dogfood_score_reports_missing_case(tmp_path: Path, capsys):
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps({"cases": [], "variant_comparisons": []}), encoding="utf-8")
    args = argparse.Namespace(
        pr_review_command="dogfood-score",
        run_json=str(run_path),
        case_id="missing",
        score_file=str(tmp_path / "scores.jsonl"),
        buckets=[],
        quality=None,
        safe_to_post="n/a",
        default_vote="inconclusive",
        notes="",
        json=True,
    )

    assert pr_review_cli.pr_review_command(args) == 1
    captured = capsys.readouterr()
    assert "was not found" in captured.out


def test_cmd_dogfood_run_refuses_invalid_observation_history(monkeypatch, tmp_path: Path, capsys):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "cases": [
            {
                "id": "known",
                "pr": "owner/repo#1",
                "category": "small-docs",
                "title": "Known",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))
    output_dir = tmp_path / "runs"
    output_dir.mkdir()
    bad_history = output_dir / "observations.jsonl"
    bad_history.write_text('{not-json}\n', encoding="utf-8")

    def fake_run_review(args, ctx=None):
        return {
            "success": True,
            "repo": "owner/repo",
            "pr": 1,
            "pr_ref": args.pr,
            "head_sha": "abc",
            "verdict": "comment",
            "model_verdict": "comment",
            "risk": "low",
            "mode": args.mode,
            "findings": 0,
            "paths": {"review": str(tmp_path / "review.md")},
            "docs_loaded": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
            "context_fingerprint": "ctx",
            "review_fingerprint": "rev",
            "comment": None,
        }

    monkeypatch.setattr(pr_review_cli, "_run_review", fake_run_review)
    args = argparse.Namespace(
        pr_review_command="dogfood-run",
        manifest=str(manifest_path),
        case_ids=[],
        limit=None,
        no_llm=True,
        max_diff_chars=5000,
        mode="balanced",
        output_dir=str(output_dir),
        run_id="unit-run",
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid JSON" in captured.out
    assert bad_history.read_text(encoding="utf-8") == '{not-json}\n'
    assert (output_dir / "unit-run.json").exists()
    assert (output_dir / "unit-run.md").exists()


def test_cmd_dogfood_run_reports_unknown_case(capsys, tmp_path: Path):
    raw = {
        "schema_version": 1,
        "name": "mini-dogfood",
        "description": "tiny harness test",
        "cases": [
            {
                "id": "known",
                "pr": "owner/repo#1",
                "category": "small-docs",
                "title": "Known",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(raw))
    args = argparse.Namespace(
        pr_review_command="dogfood-run",
        manifest=str(manifest_path),
        case_ids=["missing"],
        limit=None,
        no_llm=True,
        max_diff_chars=5000,
        mode="balanced",
        output_dir=str(tmp_path / "runs"),
        run_id="unit-run",
        json=True,
    )

    rc = pr_review_cli.pr_review_command(args)

    captured = capsys.readouterr()
    assert rc == 1
    assert "unknown eval case" in captured.out
