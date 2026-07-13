"""Dogfood evaluation, artifact, observation, and scoring workflows.

This module owns the reviewer quality-evaluation lifecycle. The CLI module keeps
argument registration and injects the live review runner so this subsystem stays
independently testable without importing the full command surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import core, evals

MANUAL_SCORING_CRITERIA: List[Dict[str, str]] = [
    {
        "key": "same_useful_finding",
        "label": "Same useful finding",
        "description": "Both variants identify substantially the same actionable issue.",
    },
    {
        "key": "baseline_sharper",
        "label": "Baseline sharper",
        "description": "Baseline cites better evidence, scope, or fix guidance for a valid issue.",
    },
    {
        "key": "graph_sharper",
        "label": "Graph sharper",
        "description": "Graph cites better evidence, scope, or fix guidance for a valid issue.",
    },
    {
        "key": "graph_missed_useful_baseline",
        "label": "Graph missed useful baseline finding",
        "description": "Baseline found a post-worthy issue that graph omitted.",
    },
    {
        "key": "baseline_missed_useful_graph",
        "label": "Baseline missed useful graph finding",
        "description": "Graph found a post-worthy issue that baseline omitted.",
    },
    {
        "key": "graph_reduced_noise",
        "label": "Graph reduced noise",
        "description": "Graph omitted a baseline finding that manual review considers noise or not post-worthy.",
    },
    {
        "key": "graph_introduced_noise",
        "label": "Graph introduced noise",
        "description": "Graph added a finding that manual review considers noise or not post-worthy.",
    },
    {
        "key": "both_noise",
        "label": "Both noise",
        "description": "Both variants produced only non-post-worthy findings for this case.",
    },
    {
        "key": "expectation_should_change",
        "label": "Expectation should change",
        "description": "The manifest expectation is stale because the finding is valid and post-worthy.",
    },
    {
        "key": "expectation_should_hold",
        "label": "Expectation should hold",
        "description": "The manifest expectation is still correct; unexpected findings should be treated as noise/regression.",
    },
]

POSTING_QUALITY_CRITERIA: List[Dict[str, str]] = [
    {
        "key": "actionable",
        "description": "The finding identifies a concrete issue introduced by the PR and a practical fix.",
    },
    {
        "key": "evidence_cited",
        "description": "The finding cites specific changed code, behavior, checks, or docs rather than vibes.",
    },
    {
        "key": "severity_reasonable",
        "description": "The risk/severity matches the likely blast radius and does not overstate uncertainty.",
    },
    {
        "key": "not_duplicate",
        "description": "The finding is not merely a duplicate wording of another finding in the same variant.",
    },
    {
        "key": "safe_to_post",
        "description": "The finding is accurate, useful, and low-enough noise that we would be comfortable posting it publicly.",
    },
]

DOGFOOD_QUALITY_BUCKETS: List[Dict[str, str]] = [
    {
        "key": "post_worthy",
        "description": "Actionable, evidence-cited, severity-appropriate, and low-noise enough to post after human confirmation.",
    },
    {
        "key": "useful_but_edit",
        "description": "Real signal, but wording, severity, or context needs adjustment before public posting.",
    },
    {
        "key": "artifact_only",
        "description": "Useful as local review evidence but not worth a GitHub comment.",
    },
    {
        "key": "noise",
        "description": "False positive, duplicate, too vague, misleading, or otherwise not useful.",
    },
    {
        "key": "miss",
        "description": "The reviewer missed an obvious issue expected for this case.",
    },
]


def _manual_scoring_guide() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "primary_question": "Would this exact variant output be safe and useful to post publicly?",
        "manual_score_buckets": MANUAL_SCORING_CRITERIA,
        "quality_buckets": DOGFOOD_QUALITY_BUCKETS,
        "posting_quality_checks": POSTING_QUALITY_CRITERIA,
        "decision_rule": (
            "Merge an experimental opt-in graph variant only when graph has no recurring useful-miss pattern, "
            "does not increase public-post noise, and is sometimes sharper or faster while baseline remains available. "
            "Do not consider default-on until graph is consistently equal-or-better on post-worthy findings."
        ),
    }


def _selected_eval_cases(manifest: evals.EvalManifest, args: argparse.Namespace) -> List[evals.EvalCase]:
    cases = manifest.cases
    wanted = set(getattr(args, "case_ids", []) or [])
    if wanted:
        known = {case.id for case in cases}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError(f"unknown eval case id(s): {', '.join(missing)}")
        cases = [case for case in cases if case.id in wanted]
    limit = getattr(args, "limit", None)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        cases = cases[:limit]
    return cases



def _compare_case_expectations(case: evals.EvalCase, result: Dict[str, Any]) -> Dict[str, Any]:
    expected = dict(case.expectations or {})
    failures: List[str] = []
    if not expected:
        return {"expected": {}, "passed": None, "failures": []}

    def fail(message: str) -> None:
        failures.append(message)

    if "expected_findings_max" in expected and int(result.get("findings") or 0) > expected["expected_findings_max"]:
        fail(f"findings {result.get('findings')} > expected max {expected['expected_findings_max']}")
    if "expected_risk" in expected and result.get("risk") != expected["expected_risk"]:
        fail(f"risk {result.get('risk')} != expected {expected['expected_risk']}")
    if "expected_truncated" in expected and bool(result.get("diff_truncated")) is not expected["expected_truncated"]:
        fail(f"diff_truncated {bool(result.get('diff_truncated'))} != expected {expected['expected_truncated']}")
    if "expected_docs_loaded_min" in expected and len(result.get("docs_loaded") or []) < expected["expected_docs_loaded_min"]:
        fail(f"docs_loaded {len(result.get('docs_loaded') or [])} < expected min {expected['expected_docs_loaded_min']}")
    if "expected_posted_comments" in expected:
        posted = 1 if result.get("comment") else 0
        if posted != expected["expected_posted_comments"]:
            fail(f"posted_comments {posted} != expected {expected['expected_posted_comments']}")

    return {"expected": expected, "passed": not failures, "failures": failures}

def _dogfood_run_id(args: argparse.Namespace) -> str:
    explicit = getattr(args, "run_id", None)
    if explicit:
        safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in explicit.strip())
        if safe.strip("-"):
            return safe.strip("-")
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_expectation_status(expectation: Dict[str, Any]) -> str:
    if not expectation or expectation.get("passed") is None:
        return "not configured"
    expected = expectation.get("expected") or {}
    expected_bits = ", ".join(f"{key}={value}" for key, value in sorted(expected.items())) or "configured"
    if expectation.get("passed"):
        return f"pass ({expected_bits})"
    failures = "; ".join(expectation.get("failures") or [])
    return f"FAIL ({failures})"

def _render_dogfood_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        f"# PR Review Dogfood Run — {summary['run_id']}",
        "",
        f"- Manifest: `{summary['manifest']['name']}`",
        f"- Mode: `{summary['mode']}`",
        f"- Variants: `{', '.join(summary.get('variants') or ['baseline'])}`",
        f"- No LLM: `{str(summary['no_llm']).lower()}`",
        f"- Cases: {summary['case_count']}",
        f"- Successes: {summary['success_count']}",
        f"- Failures: {summary['failure_count']}",
        f"- Total findings: {summary['total_findings']}",
        f"- Truncated diffs: {summary['truncated_count']}",
        f"- Comments posted: {summary['posted_comment_count']} (should stay 0 for dogfood)",
        f"- Expectation failures: {summary['expectation_failure_count']}",
        f"- Observation history: `{(summary.get('paths') or {}).get('observations_summary', '')}`",
        "",
        "| Case | Variant | PR | Status | Expected | Findings | Risk | Time | Truncated | Docs | Skipped | Checks | Artifact |",
        "|---|---|---|---|---|---:|---|---:|---|---:|---:|---|---|",
    ]
    for item in summary["cases"]:
        if item.get("success"):
            checks = item.get("check_context") or {}
            counts = checks.get("counts") if isinstance(checks, dict) else None
            check_bits = ", ".join(f"{key}:{value}" for key, value in sorted((counts or {}).items())) or "not observed"
            artifact = item.get("paths", {}).get("review", "")
            expectation = item.get("expectation") or {}
            passed = expectation.get("passed")
            expected_bits = "n/a" if passed is None else ("pass" if passed else "FAIL")
            elapsed = item.get("elapsed_sec")
            elapsed_text = f"{float(elapsed):.2f}s" if isinstance(elapsed, (int, float)) else ""
            lines.append(
                f"| `{item['case_id']}` | `{item.get('variant', 'baseline')}` | `{item['pr_ref']}` | ok | {expected_bits} | {item['findings']} | {item['risk']} | "
                f"{elapsed_text} | {str(item['diff_truncated']).lower()} | {len(item.get('docs_loaded') or [])} | "
                f"{len(item.get('skipped_files') or [])} | {check_bits} | `{artifact}` |"
            )
        else:
            lines.append(
                f"| `{item['case_id']}` | `{item.get('variant', 'baseline')}` | `{item['pr_ref']}` | failed | FAIL |  |  |  |  |  |  | `{item.get('error', '')}` |  |"
            )
    lines.extend([
        "",
        "## Variant comparison",
        "",
    ])
    comparisons = summary.get("variant_comparisons") or []
    if comparisons:
        lines.extend([
            "| Case | PR | Baseline | Graph | Δ findings | Δ time | Baseline artifact | Graph artifact |",
            "|---|---|---:|---:|---:|---:|---|---|",
        ])
        for item in comparisons:
            time_delta = item.get("elapsed_delta_sec")
            time_delta_text = f"{float(time_delta):.2f}s" if isinstance(time_delta, (int, float)) else ""
            lines.append(
                f"| `{item['case_id']}` | `{item.get('pr_ref', '')}` | {item.get('baseline_findings')} {item.get('baseline_risk') or ''} | "
                f"{item.get('graph_findings')} {item.get('graph_risk') or ''} | {item.get('finding_delta')} | {time_delta_text} | "
                f"`{item.get('baseline_review') or ''}` | `{item.get('graph_review') or ''}` |"
            )
    else:
        lines.append("No paired baseline/graph variants in this run.")
    scoring = summary.get("manual_scoring") or {}
    bucket_rows = scoring.get("manual_score_buckets") or []
    quality_rows = scoring.get("posting_quality_checks") or []
    lines.extend([
        "",
        "## Manual scoring guide",
        "",
        f"Primary question: {scoring.get('primary_question') or 'Would this output be safe and useful to post publicly?'}",
        "",
        "Score each baseline/graph pair by post-worthy behavior, not raw finding count. Use one or more bucket keys when manually reviewing artifacts:",
        "",
    ])
    for bucket in bucket_rows:
        lines.append(f"- `{bucket.get('key')}` — {bucket.get('description')}")
    lines.extend([
        "",
        "Posting-quality checks for each finding:",
        "",
    ])
    for check in quality_rows:
        lines.append(f"- `{check.get('key')}` — {check.get('description')}")
    lines.extend([
        "",
        f"Decision rule: {scoring.get('decision_rule') or 'Keep experimental variants opt-in until post-worthy evidence is consistently equal-or-better.'}",
        "",
        "## Cases",
        "",
    ])
    for item in summary["cases"]:
        lines.append(f"### `{item['case_id']}` / `{item.get('variant', 'baseline')}` — `{item['pr_ref']}`")
        if item.get("success"):
            lines.extend([
                "",
                f"- Artifact path: `{item.get('paths', {}).get('review', '')}`",
                f"- Findings count: {item['findings']}",
                f"- Risk: {item['risk']}",
                f"- Elapsed: {item.get('elapsed_sec', 'not recorded')}s",
                f"- Docs loaded: {', '.join(item.get('docs_loaded') or []) or 'none'}",
                f"- Skipped files: {len(item.get('skipped_files') or [])}",
                f"- Diff truncated: {item['diff_truncated']}",
                f"- GitHub check metadata: {'observed' if (item.get('check_context') or {}).get('observed') else 'not observed'}",
                "- Would post publicly? no — dogfood runner never sets `--post-comment`.",
                f"- Expectations: {_format_expectation_status(item.get('expectation') or {})}",
                "- Manual scoring:",
                "  - Bucket(s): TODO (`same_useful_finding`, `graph_sharper`, `baseline_sharper`, `graph_reduced_noise`, etc.)",
                "  - Safe to post publicly? TODO (yes/no + why)",
                "  - Useful misses/noise: TODO",
                "  - Expectation update needed? TODO",
                "- Follow-up patch needed:",
                "  - TODO: fill after manual inspection.",
                "",
            ])
        else:
            lines.extend(["", f"- Error: `{item.get('error', '')}`", ""])
    return "\n".join(lines).rstrip() + "\n"



def _compact_case_observation(item: Dict[str, Any]) -> Dict[str, Any]:
    expectation = item.get("expectation") or {}
    return {
        "variant": item.get("variant") or "baseline",
        "case_id": item.get("case_id"),
        "pr_ref": item.get("pr_ref"),
        "success": bool(item.get("success")),
        "findings": int(item.get("findings") or 0) if item.get("success") else None,
        "risk": item.get("risk") if item.get("success") else None,
        "diff_truncated": bool(item.get("diff_truncated")) if item.get("success") else None,
        "elapsed_sec": item.get("elapsed_sec") if item.get("success") else None,
        "docs_loaded_count": len(item.get("docs_loaded") or []) if item.get("success") else 0,
        "skipped_files_count": len(item.get("skipped_files") or []) if item.get("success") else 0,
        "expectation_passed": expectation.get("passed"),
        "expectation_failures": list(expectation.get("failures") or []),
        "artifact_review": (item.get("paths") or {}).get("review"),
        "error": item.get("error"),
    }


def _observation_record(summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "run_id": summary.get("run_id"),
        "manifest": (summary.get("manifest") or {}).get("name"),
        "manifest_identity": (summary.get("manifest") or {}).get("identity"),
        "mode": summary.get("mode"),
        "variants": list(summary.get("variants") or ["baseline"]),
        "no_llm": bool(summary.get("no_llm")),
        "success": bool(summary.get("success")),
        "case_count": summary.get("case_count"),
        "success_count": summary.get("success_count"),
        "failure_count": summary.get("failure_count"),
        "total_findings": summary.get("total_findings"),
        "truncated_count": summary.get("truncated_count"),
        "posted_comment_count": summary.get("posted_comment_count"),
        "expectation_failure_count": summary.get("expectation_failure_count"),
        "case_ids": [item.get("case_id") for item in summary.get("cases", [])],
        "cases": [_compact_case_observation(item) for item in summary.get("cases", [])],
    }


def _summarize_observations(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_case: Dict[str, Dict[str, Any]] = {}
    for record in records:
        for case in record.get("cases", []):
            case_id = str(case.get("case_id") or "")
            if not case_id:
                continue
            manifest_identity = record.get("manifest_identity")
            variant = str(case.get("variant") or "baseline")
            summary_key = f"{manifest_identity or record.get('manifest') or ''}\0{case_id}\0{variant}"
            bucket = by_case.setdefault(
                summary_key,
                {
                    "case_id": case_id,
                    "variant": variant,
                    "manifest": record.get("manifest"),
                    "manifest_identity": manifest_identity,
                    "pr_ref": case.get("pr_ref"),
                    "runs": 0,
                    "successful_runs": 0,
                    "findings_values": [],
                    "risk_values": [],
                    "truncated_values": [],
                    "elapsed_values": [],
                    "expectation_failures": 0,
                    "expectation_failure_messages": [],
                },
            )
            bucket["runs"] += 1
            if case.get("success"):
                bucket["successful_runs"] += 1
                bucket["findings_values"].append(int(case.get("findings") or 0))
                if case.get("risk") is not None:
                    bucket["risk_values"].append(case.get("risk"))
                if case.get("diff_truncated") is not None:
                    bucket["truncated_values"].append(bool(case.get("diff_truncated")))
                if isinstance(case.get("elapsed_sec"), (int, float)):
                    bucket["elapsed_values"].append(float(case.get("elapsed_sec")))
            failures = list(case.get("expectation_failures") or [])
            if failures:
                bucket["expectation_failures"] += 1
                bucket["expectation_failure_messages"].extend(failures)

    cases = []
    for case_id, bucket in sorted(by_case.items()):
        findings = bucket.pop("findings_values")
        risks = bucket.pop("risk_values")
        truncated = bucket.pop("truncated_values")
        elapsed = bucket.pop("elapsed_values")
        bucket["findings_min"] = min(findings) if findings else None
        bucket["findings_max"] = max(findings) if findings else None
        bucket["findings_values"] = sorted(set(findings))
        bucket["risk_values"] = sorted(set(risks))
        bucket["truncated_values"] = sorted(set(truncated))
        bucket["elapsed_min_sec"] = round(min(elapsed), 3) if elapsed else None
        bucket["elapsed_max_sec"] = round(max(elapsed), 3) if elapsed else None
        bucket["elapsed_avg_sec"] = round(sum(elapsed) / len(elapsed), 3) if elapsed else None
        bucket["stable_findings"] = len(set(findings)) <= 1 if findings else None
        bucket["stable_risk"] = len(set(risks)) <= 1 if risks else None
        cases.append(bucket)

    return {
        "schema_version": 1,
        "run_count": len(records),
        "case_count": len(cases),
        "total_expectation_failure_runs": sum(int(record.get("expectation_failure_count") or 0) for record in records),
        "cases": cases,
    }


def _atomic_write_text(path: Path, content: str) -> None:
    core._write_private_text(path, content)


def _read_observation_records(history_path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not history_path.exists():
        return records
    for line_number, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            existing = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {history_path} line {line_number}: {exc.msg}") from exc
        if not isinstance(existing, dict):
            raise ValueError(f"invalid observation record in {history_path} line {line_number}: expected object")
        cases = existing.get("cases", [])
        if not isinstance(cases, list) or any(not isinstance(case, dict) for case in cases):
            raise ValueError(f"invalid observation record in {history_path} line {line_number}: cases must be an array of objects")
        records.append(existing)
    return records


def _lock_observation_file(lock_handle) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
        import msvcrt

        lock_handle.seek(0)
        lock_handle.write("0")
        lock_handle.flush()
        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)


def _unlock_observation_file(lock_handle) -> None:
    if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
        import msvcrt

        lock_handle.seek(0)
        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _write_observation_history(summary: Dict[str, Any], output_dir: str | Path) -> Dict[str, str]:
    root = Path(output_dir)
    history_path = root / "observations.jsonl"
    summary_path = root / "observations-summary.json"
    lock_path = root / "observations.lock"
    record = _observation_record(summary)
    core._secure_artifact_directory(root)
    if lock_path.is_symlink():
        raise ValueError(f"dogfood observation lock must not be a symlink: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(fd, "a+", encoding="utf-8") as lock_handle:
        _lock_observation_file(lock_handle)
        try:
            records = []
            for existing in _read_observation_records(history_path):
                same_run = (
                    existing.get("run_id") == record.get("run_id")
                    and existing.get("manifest_identity") == record.get("manifest_identity")
                    and existing.get("case_ids") == record.get("case_ids")
                    and existing.get("mode") == record.get("mode")
                    and existing.get("variants") == record.get("variants")
                    and bool(existing.get("no_llm")) == bool(record.get("no_llm"))
                )
                if not same_run:
                    records.append(existing)
            records.append(record)
            history_content = "".join(json.dumps(item, sort_keys=True) + "\n" for item in records)
            summary_content = json.dumps(_summarize_observations(records), indent=2, sort_keys=True) + "\n"
            _atomic_write_text(history_path, history_content)
            _atomic_write_text(summary_path, summary_content)
        finally:
            _unlock_observation_file(lock_handle)
    return {"observations": str(history_path), "observations_summary": str(summary_path)}

def _write_dogfood_summary(summary: Dict[str, Any], output_dir: str | Path) -> Dict[str, str]:
    root = Path(output_dir)
    core._secure_artifact_directory(root)
    base = root / summary["run_id"]
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    paths = {
        "json": str(json_path),
        "markdown": str(md_path),
        "observations": str(root / "observations.jsonl"),
        "observations_summary": str(root / "observations-summary.json"),
    }
    summary["paths"] = paths
    _atomic_write_text(json_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(md_path, _render_dogfood_markdown(summary))
    paths.update(_write_observation_history(summary, root))
    summary["paths"] = paths
    _atomic_write_text(json_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    _atomic_write_text(md_path, _render_dogfood_markdown(summary))
    return paths


def _dogfood_manifest_identity(path: str | Path | None) -> Dict[str, str]:
    manifest_path = Path(path) if path else evals.default_manifest_path()
    content = manifest_path.read_bytes()
    return {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "identity": f"{manifest_path}:{hashlib.sha256(content).hexdigest()[:16]}",
    }


def _safe_artifact_stem(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value.strip())
    return safe.strip("-") or "case"


def _copy_dogfood_artifacts(payload: Dict[str, Any], *, output_dir: str | Path, run_id: str, case_id: str, variant: str) -> None:
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return
    artifact_root = Path(output_dir) / f"{run_id}-artifacts"
    core._secure_artifact_directory(artifact_root)
    stem = f"{_safe_artifact_stem(case_id)}__{_safe_artifact_stem(variant)}"
    copied: Dict[str, str] = {}
    for key, raw_path in list(paths.items()):
        if not raw_path:
            continue
        src = Path(str(raw_path))
        if not src.exists() or not src.is_file():
            continue
        dest = artifact_root / f"{stem}.{key}{src.suffix or '.txt'}"
        core._write_private_text(dest, src.read_text(encoding="utf-8"))
        copied[key] = str(dest)
    if copied:
        payload["source_paths"] = dict(paths)
        payload["paths"] = {**paths, **copied}


def _dogfood_variants(args: argparse.Namespace) -> List[str]:
    variants = list(getattr(args, "variants", []) or [])
    if not variants:
        return ["baseline"]
    ordered: List[str] = []
    for variant in variants:
        if variant not in ("baseline", "graph"):
            raise ValueError(f"unsupported dogfood variant: {variant}")
        if variant not in ordered:
            ordered.append(variant)
    return ordered


def _load_graph_local_repo_map(value: str | None) -> Dict[str, str]:
    if not value:
        return {}
    text = value.strip()
    raw = json.loads(text if text.startswith("{") else Path(text).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("--graph-local-repo-map must be a JSON object mapping case ids or PR refs to local repos")
    mapping: Dict[str, str] = {}
    for key, path in raw.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(path, str) or not path.strip():
            raise ValueError("--graph-local-repo-map entries must be non-empty string keys and paths")
        mapping[key.strip()] = path.strip()
    return mapping


def _graph_local_repo_for_case(case: evals.EvalCase, mapping: Dict[str, str]) -> str:
    local_repo = mapping.get(case.id) or mapping.get(case.pr)
    if not local_repo:
        raise ValueError(f"graph variant requires local repo mapping for case `{case.id}` or `{case.pr}`")
    return local_repo


def _review_args_for_dogfood_case(case: evals.EvalCase, args: argparse.Namespace, *, variant: str, graph_map: Dict[str, str]) -> argparse.Namespace:
    review_args = argparse.Namespace(
        pr=case.pr,
        no_llm=bool(getattr(args, "no_llm", False)),
        dry_run=bool(getattr(args, "no_llm", False)),
        max_diff_chars=getattr(args, "max_diff_chars", 120_000),
        post_comment=False,
        allow_truncated_post=False,
        json=True,
        mode=getattr(args, "mode", "balanced"),
        graph_context=False,
        local_repo=None,
        graph_context_binary=None,
        graph_index_mode="fast",
        max_graph_context_chars=core.MAX_GRAPH_CONTEXT_CHARS,
    )
    if variant == "graph":
        review_args.graph_context = True
        review_args.local_repo = _graph_local_repo_for_case(case, graph_map)
        review_args.graph_context_binary = getattr(args, "graph_context_binary", None)
        review_args.graph_index_mode = getattr(args, "graph_index_mode", "fast") or "fast"
        review_args.max_graph_context_chars = max(1_000, int(getattr(args, "max_graph_context_chars", core.MAX_GRAPH_CONTEXT_CHARS)))
    return review_args


def _compare_variant_pairs(case_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_case: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for item in case_results:
        by_case.setdefault(str(item.get("case_id") or ""), {})[str(item.get("variant") or "baseline")] = item
    comparisons: List[Dict[str, Any]] = []
    for case_id, variants in sorted(by_case.items()):
        baseline = variants.get("baseline")
        graph = variants.get("graph")
        if not baseline or not graph:
            continue
        baseline_findings = int(baseline.get("findings") or 0) if baseline.get("success") else None
        graph_findings = int(graph.get("findings") or 0) if graph.get("success") else None
        baseline_elapsed = baseline.get("elapsed_sec")
        graph_elapsed = graph.get("elapsed_sec")
        elapsed_delta = None
        if isinstance(baseline_elapsed, (int, float)) and isinstance(graph_elapsed, (int, float)):
            elapsed_delta = round(float(graph_elapsed) - float(baseline_elapsed), 3)
        comparisons.append(
            {
                "case_id": case_id,
                "pr_ref": baseline.get("pr_ref") or graph.get("pr_ref"),
                "baseline_success": bool(baseline.get("success")),
                "graph_success": bool(graph.get("success")),
                "baseline_findings": baseline_findings,
                "graph_findings": graph_findings,
                "finding_delta": None if baseline_findings is None or graph_findings is None else graph_findings - baseline_findings,
                "baseline_risk": baseline.get("risk"),
                "graph_risk": graph.get("risk"),
                "baseline_elapsed_sec": baseline_elapsed,
                "graph_elapsed_sec": graph_elapsed,
                "elapsed_delta_sec": elapsed_delta,
                "baseline_review": (baseline.get("paths") or {}).get("review"),
                "graph_review": (graph.get("paths") or {}).get("review"),
                "graph_context": graph.get("graph_context"),
            }
        )
    return comparisons


def _load_dogfood_run(path: str | Path) -> Dict[str, Any]:
    run_path = Path(path)
    data = json.loads(run_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("dogfood run JSON must be an object")
    if not isinstance(data.get("cases"), list):
        raise ValueError("dogfood run JSON is missing cases array")
    return data


def _case_score_context(summary: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    for item in summary.get("variant_comparisons") or []:
        if item.get("case_id") == case_id:
            return {"score_context": "variant_comparison", **dict(item)}
    matching = [item for item in summary.get("cases") or [] if item.get("case_id") == case_id]
    if not matching:
        raise ValueError(f"case `{case_id}` was not found in this dogfood run")
    successful = [item for item in matching if item.get("success")]
    if len(successful) != 1:
        raise ValueError(
            f"case `{case_id}` has {len(successful)} successful variants and no paired baseline/graph comparison; "
            "score a run with exactly one successful variant or a paired comparison"
        )
    case = dict(successful[0])
    return {
        "score_context": "single_variant",
        "case_id": case_id,
        "variant": case.get("variant"),
        "pr_ref": case.get("pr_ref"),
        "findings": case.get("findings"),
        "risk": case.get("risk"),
        "diff_truncated": case.get("diff_truncated"),
        "docs_loaded": case.get("docs_loaded") or [],
        "skipped_files": case.get("skipped_files") or [],
        "check_counts": (case.get("check_context") or {}).get("counts") or {},
        "artifact": (case.get("paths") or {}).get("review"),
        "expectation": case.get("expectation") or {},
    }


def _case_comparison(summary: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    context = _case_score_context(summary, case_id)
    if context.get("score_context") != "variant_comparison":
        raise ValueError(f"case `{case_id}` does not have a paired baseline/graph comparison in this run")
    context.pop("score_context", None)
    return context


def _write_score_record(score_file: Path, record: Dict[str, Any]) -> None:
    core._secure_artifact_directory(score_file.parent)
    if score_file.is_symlink():
        raise ValueError(f"dogfood score file must not be a symlink: {score_file}")
    existed = score_file.exists()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(score_file, flags, 0o600)
    os.chmod(score_file, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if not existed:
        core._fsync_directory(score_file.parent)


def _read_score_records(score_file: Path) -> List[Dict[str, Any]]:
    if not score_file.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(score_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in {score_file} line {line_number}: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"invalid score record in {score_file} line {line_number}: expected object")
        records.append(item)
    return records


def _score_report(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    vote_counts: Dict[str, int] = {"graph_better": 0, "equivalent": 0, "baseline_better": 0, "inconclusive": 0}
    bucket_counts: Dict[str, int] = {}
    quality_counts: Dict[str, int] = {item["key"]: 0 for item in DOGFOOD_QUALITY_BUCKETS}
    safe_counts: Dict[str, int] = {"yes": 0, "no": 0, "n/a": 0}
    context_counts: Dict[str, int] = {}
    truncated_records = 0
    truncated_public_posting_records = 0
    graph_scored = 0
    case_ids = sorted({str(item.get("case_id")) for item in records if item.get("case_id")})
    for item in records:
        safe = str(item.get("safe_to_post") or "n/a")
        safe_counts[safe] = safe_counts.get(safe, 0) + 1
        quality = item.get("quality")
        if quality:
            quality_counts[str(quality)] = quality_counts.get(str(quality), 0) + 1
        context = item.get("score_context") or item.get("comparison") or {}
        context_kind = str(context.get("score_context") or "variant_comparison")
        context_counts[context_kind] = context_counts.get(context_kind, 0) + 1
        is_graph_score = context_kind == "variant_comparison"
        if is_graph_score:
            graph_scored += 1
            vote = str(item.get("default_vote") or "inconclusive")
            vote_counts[vote] = vote_counts.get(vote, 0) + 1
        if context.get("diff_truncated"):
            truncated_records += 1
            if safe == "yes" or quality == "post_worthy":
                truncated_public_posting_records += 1
        for bucket in item.get("buckets") or []:
            key = str(bucket)
            bucket_counts[key] = bucket_counts.get(key, 0) + 1
    scored = len(records)
    negative = bucket_counts.get("graph_missed_useful_baseline", 0) + bucket_counts.get("graph_introduced_noise", 0)
    positive = vote_counts.get("graph_better", 0) + vote_counts.get("equivalent", 0)
    graph_readiness = "insufficient_data"
    if graph_scored >= 12 and negative == 0 and positive / max(graph_scored, 1) >= 0.8:
        graph_readiness = "consider_default_on"
    elif graph_scored >= 3:
        graph_readiness = "keep_default_off"

    quality_negative = quality_counts.get("noise", 0) + quality_counts.get("miss", 0)
    post_worthy = quality_counts.get("post_worthy", 0)
    safe_post_worthy = sum(
        1 for item in records if item.get("quality") == "post_worthy" and str(item.get("safe_to_post") or "n/a") == "yes"
    )
    unsafe_to_post = safe_counts.get("no", 0)
    quality_scored = sum(quality_counts.values())
    posting_readiness = "insufficient_data"
    if (
        quality_scored >= 5
        and quality_negative == 0
        and post_worthy >= 1
        and safe_post_worthy >= 1
        and unsafe_to_post == 0
        and truncated_public_posting_records == 0
    ):
        posting_readiness = "ready_for_repo_canary"
    elif quality_scored >= 3 and quality_negative == 0:
        posting_readiness = "hold_posting_collect_more"
    elif quality_negative > 0:
        posting_readiness = "needs_tuning"
    return {
        "schema_version": 1,
        "scored_records": scored,
        "graph_scored_records": graph_scored,
        "scored_cases": len(case_ids),
        "case_ids": case_ids,
        "default_vote_counts": dict(sorted(vote_counts.items())),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "quality_counts": dict(sorted(quality_counts.items())),
        "safe_to_post_counts": dict(sorted(safe_counts.items())),
        "score_context_counts": dict(sorted(context_counts.items())),
        "truncated_records": truncated_records,
        "truncated_public_posting_records": truncated_public_posting_records,
        "unsafe_to_post_records": unsafe_to_post,
        "recommendation": graph_readiness,
        "graph_recommendation": graph_readiness,
        "posting_recommendation": posting_readiness,
        "default_on_gate": "Consider graph default-on only after >=12 scored records, no graph useful-miss/noise pattern, and >=80% graph_better/equivalent votes.",
        "posting_gate": "Consider repo-level findings-only canary after >=5 quality-scored no-post cases, no noise/miss pattern, at least one post_worthy finding, and no truncated public posting.",
    }


def _render_score_report(report: Dict[str, Any]) -> str:
    lines = [
        "PR review dogfood score report",
        "==============================",
        f"Scored records: {report['scored_records']}",
        f"Graph-scored records: {report.get('graph_scored_records', 0)}",
        f"Scored cases: {report['scored_cases']}",
        f"Graph recommendation: {report.get('graph_recommendation', report.get('recommendation'))}",
        f"Posting recommendation: {report.get('posting_recommendation', 'insufficient_data')}",
        f"Truncated scored records: {report.get('truncated_records', 0)}",
        f"Truncated public-post candidates: {report.get('truncated_public_posting_records', 0)}",
        f"Unsafe-to-post records: {report.get('unsafe_to_post_records', 0)}",
        "",
        "Quality buckets:",
    ]
    quality_counts = report.get("quality_counts") or {}
    if any(quality_counts.values()):
        for key, count in quality_counts.items():
            if count:
                lines.append(f"  - {key}: {count}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Default votes:")
    for key, count in (report.get("default_vote_counts") or {}).items():
        lines.append(f"  - {key}: {count}")
    lines.append("")
    lines.append("Manual buckets:")
    bucket_counts = report.get("bucket_counts") or {}
    if bucket_counts:
        for key, count in bucket_counts.items():
            lines.append(f"  - {key}: {count}")
    else:
        lines.append("  - none")
    lines.extend(["", report["posting_gate"], report["default_on_gate"]])
    return "\n".join(lines) + "\n"


def cmd_dogfood_score(args: argparse.Namespace) -> int:
    try:
        summary = _load_dogfood_run(getattr(args, "run_json"))
        case_id = str(getattr(args, "case_id"))
        score_context = _case_score_context(summary, case_id)
        buckets = list(dict.fromkeys(getattr(args, "buckets", []) or []))
        context_kind = score_context.get("score_context")
        quality = getattr(args, "quality", None)
        default_vote = getattr(args, "default_vote", None)
        if not quality:
            raise ValueError("--quality is required when scoring dogfood runs")
        if context_kind == "variant_comparison" and not default_vote:
            raise ValueError("--default-vote is required when scoring paired baseline/graph runs")
        record = {
            "schema_version": 1,
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "run_id": summary.get("run_id"),
            "run_json": str(Path(getattr(args, "run_json"))),
            "manifest": (summary.get("manifest") or {}).get("name"),
            "case_id": case_id,
            "pr_ref": score_context.get("pr_ref"),
            "quality": quality,
            "buckets": buckets,
            "safe_to_post": getattr(args, "safe_to_post", "n/a"),
            "default_vote": default_vote or "inconclusive",
            "notes": str(getattr(args, "notes", "") or ""),
            "score_context": score_context,
            "comparison": score_context if score_context.get("score_context") == "variant_comparison" else None,
        }
        _write_score_record(Path(getattr(args, "score_file")), record)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            print(f"hermes pr-review dogfood-score: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"success": True, "record": record}, indent=2, sort_keys=True))
    else:
        print(f"Appended score for {record['case_id']} to {getattr(args, 'score_file')}")
    return 0


def cmd_dogfood_report(args: argparse.Namespace) -> int:
    try:
        records = _read_score_records(Path(getattr(args, "score_file")))
        report = _score_report(records)
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            print(f"hermes pr-review dogfood-report: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"success": True, **report}, indent=2, sort_keys=True))
    else:
        print(_render_score_report(report), end="")
    return 0


def cmd_dogfood_run(args: argparse.Namespace, *, review_runner, ctx=None) -> int:
    try:
        manifest = evals.load_eval_manifest(getattr(args, "manifest", None))
        manifest_path_info = _dogfood_manifest_identity(getattr(args, "manifest", None))
        cases = _selected_eval_cases(manifest, args)
        if not cases:
            raise ValueError("no eval cases selected")
        variants = _dogfood_variants(args)
        graph_map = _load_graph_local_repo_map(getattr(args, "graph_local_repo_map", None))
        if "graph" in variants and not graph_map:
            raise ValueError("graph variant requires --graph-local-repo-map")
        run_id = _dogfood_run_id(args)
        case_results: List[Dict[str, Any]] = []
        for case in cases:
            for variant in variants:
                try:
                    review_args = _review_args_for_dogfood_case(case, args, variant=variant, graph_map=graph_map)
                    started = time.perf_counter()
                    payload = review_runner(review_args, ctx=ctx)
                    payload["elapsed_sec"] = round(time.perf_counter() - started, 3)
                    _copy_dogfood_artifacts(
                        payload,
                        output_dir=getattr(args, "output_dir", "evals/dogfood-runs"),
                        run_id=run_id,
                        case_id=case.id,
                        variant=variant,
                    )
                    expectation = _compare_case_expectations(case, payload)
                    case_results.append({"case_id": case.id, "title": case.title, "variant": variant, "expectation": expectation, **payload})
                except Exception as exc:
                    case_results.append({"case_id": case.id, "title": case.title, "variant": variant, "pr_ref": case.pr, "success": False, "error": str(exc)})
        successes = [item for item in case_results if item.get("success")]
        expectation_failures = [
            failure
            for item in case_results
            for failure in ((item.get("expectation") or {}).get("failures") or [])
        ]
        summary: Dict[str, Any] = {
            "success": all(item.get("success") and (item.get("expectation") or {}).get("passed", True) is not False for item in case_results),
            "run_id": run_id,
            "mode": getattr(args, "mode", "balanced"),
            "variants": variants,
            "no_llm": bool(getattr(args, "no_llm", False)),
            "manifest": {
                "name": manifest.name,
                "description": manifest.description,
                "observed_at": manifest.observed_at,
                "schema_version": manifest.schema_version,
                **manifest_path_info,
            },
            "case_count": len(case_results),
            "success_count": len(successes),
            "failure_count": len(case_results) - len(successes),
            "total_findings": sum(int(item.get("findings") or 0) for item in successes),
            "truncated_count": sum(1 for item in successes if item.get("diff_truncated")),
            "posted_comment_count": sum(1 for item in successes if item.get("comment")),
            "expectation_failure_count": len(expectation_failures),
            "expectation_failures": expectation_failures,
            "manual_scoring": _manual_scoring_guide(),
            "variant_comparisons": _compare_variant_pairs(case_results),
            "cases": case_results,
        }
        paths = _write_dogfood_summary(summary, getattr(args, "output_dir", "evals/dogfood-runs"))
        summary["paths"] = paths
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            print(f"hermes pr-review dogfood-run: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Hermes PR review dogfood run {summary['run_id']}")
        print(f"  cases   : {summary['case_count']}")
        print(f"  findings: {summary['total_findings']}")
        print(f"  failures: {summary['failure_count']}")
        print(f"  expectation failures: {summary['expectation_failure_count']}")
        print(f"  markdown: {summary['paths']['markdown']}")
        print(f"  json    : {summary['paths']['json']}")
    return 0 if summary.get("success") else 1
