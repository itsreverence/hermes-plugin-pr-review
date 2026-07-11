"""Public PR evaluation manifest helpers for Hermes PR Reviewer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping

MANIFEST_SCHEMA_VERSION = 1


EXPECTATION_RISK_LEVELS = {"low", "medium", "high"}
EXPECTATION_KEYS = {
    "expected_findings_max",
    "expected_risk",
    "expected_truncated",
    "expected_docs_loaded_min",
    "expected_posted_comments",
}

CASE_CATEGORIES = {
    "small-docs",
    "frontend",
    "backend",
    "security",
    "browser-tooling",
    "ci-failing",
    "generated-dependency-heavy",
    "compiler-parser",
    "large-stress",
}


@dataclass(frozen=True)
class EvalCase:
    id: str
    pr: str
    category: str
    title: str
    observed_head_sha: str
    observed_check_status: Dict[str, int]
    changed_files: int | None = None
    additions: int | None = None
    deletions: int | None = None
    rationale: str = ""
    expectations: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalManifest:
    schema_version: int
    name: str
    description: str
    observed_at: str | None
    cases: List[EvalCase]


def default_manifest_path() -> Path:
    return Path(__file__).with_name("evals") / "public_prs.json"


def _require_str(data: Mapping[str, Any], key: str, *, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _optional_int(data: Mapping[str, Any], key: str, *, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where}.{key} must be a non-negative integer")
    return value


def _check_counts(value: Any, *, where: str) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}.observed_check_status must be an object of status counts")
    out: Dict[str, int] = {}
    for raw_key, raw_count in value.items():
        key = str(raw_key).strip().lower()
        if not key:
            raise ValueError(f"{where}.observed_check_status contains an empty status key")
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0:
            raise ValueError(f"{where}.observed_check_status.{key} must be a non-negative integer")
        out[key] = raw_count
    return out


def _expectations(value: Any, *, where: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{where}.expectations must be an object when present")
    unknown = sorted(str(key) for key in value if key not in EXPECTATION_KEYS)
    if unknown:
        raise ValueError(f"{where}.expectations contains unsupported key(s): {', '.join(unknown)}")
    out: Dict[str, Any] = {}
    for key in ("expected_findings_max", "expected_docs_loaded_min", "expected_posted_comments"):
        if key in value:
            raw = value[key]
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                raise ValueError(f"{where}.expectations.{key} must be a non-negative integer")
            out[key] = raw
    if "expected_risk" in value:
        raw_risk = value["expected_risk"]
        if not isinstance(raw_risk, str) or raw_risk not in EXPECTATION_RISK_LEVELS:
            allowed = ", ".join(sorted(EXPECTATION_RISK_LEVELS))
            raise ValueError(f"{where}.expectations.expected_risk must be one of: {allowed}")
        out["expected_risk"] = raw_risk
    if "expected_truncated" in value:
        raw_truncated = value["expected_truncated"]
        if not isinstance(raw_truncated, bool):
            raise ValueError(f"{where}.expectations.expected_truncated must be a boolean")
        out["expected_truncated"] = raw_truncated
    return out

def parse_eval_manifest(data: Mapping[str, Any]) -> EvalManifest:
    version = data.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {MANIFEST_SCHEMA_VERSION}")
    name = _require_str(data, "name", where="manifest")
    description = _require_str(data, "description", where="manifest")
    observed_at = data.get("observed_at")
    if observed_at is not None and not isinstance(observed_at, str):
        raise ValueError("manifest.observed_at must be a string when present")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("manifest.cases must be a non-empty array")

    cases: List[EvalCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_cases):
        where = f"manifest.cases[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        case_id = _require_str(item, "id", where=where)
        if case_id in seen_ids:
            raise ValueError(f"duplicate eval case id: {case_id}")
        seen_ids.add(case_id)
        category = _require_str(item, "category", where=where)
        if category not in CASE_CATEGORIES:
            allowed = ", ".join(sorted(CASE_CATEGORIES))
            raise ValueError(f"{where}.category must be one of: {allowed}")
        cases.append(
            EvalCase(
                id=case_id,
                pr=_require_str(item, "pr", where=where),
                category=category,
                title=_require_str(item, "title", where=where),
                observed_head_sha=_require_str(item, "observed_head_sha", where=where),
                observed_check_status=_check_counts(item.get("observed_check_status"), where=where),
                changed_files=_optional_int(item, "changed_files", where=where),
                additions=_optional_int(item, "additions", where=where),
                deletions=_optional_int(item, "deletions", where=where),
                rationale=str(item.get("rationale") or ""),
                expectations=_expectations(item.get("expectations"), where=where),
            )
        )
    return EvalManifest(
        schema_version=version,
        name=name,
        description=description,
        observed_at=observed_at,
        cases=cases,
    )


def load_eval_manifest(path: str | Path | None = None) -> EvalManifest:
    manifest_path = Path(path) if path else default_manifest_path()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("eval manifest root must be an object")
    return parse_eval_manifest(data)


def summarize_eval_manifest(manifest: EvalManifest) -> Dict[str, Any]:
    categories: Dict[str, int] = {}
    check_status: Dict[str, int] = {}
    total_changed_files = 0
    total_additions = 0
    total_deletions = 0
    expectation_cases = 0
    for case in manifest.cases:
        categories[case.category] = categories.get(case.category, 0) + 1
        for key, count in case.observed_check_status.items():
            check_status[key] = check_status.get(key, 0) + count
        total_changed_files += case.changed_files or 0
        total_additions += case.additions or 0
        total_deletions += case.deletions or 0
        if case.expectations:
            expectation_cases += 1
    return {
        "name": manifest.name,
        "schema_version": manifest.schema_version,
        "observed_at": manifest.observed_at,
        "case_count": len(manifest.cases),
        "categories": dict(sorted(categories.items())),
        "observed_check_status": dict(sorted(check_status.items())),
        "expectation_cases": expectation_cases,
        "totals": {
            "changed_files": total_changed_files,
            "additions": total_additions,
            "deletions": total_deletions,
        },
        "prs": [case.pr for case in manifest.cases],
    }


def render_eval_summary(manifest: EvalManifest) -> str:
    summary = summarize_eval_manifest(manifest)
    lines = [
        f"{manifest.name} (schema v{manifest.schema_version})",
        f"Cases: {summary['case_count']}",
        f"Observed at: {manifest.observed_at or 'not recorded'}",
        "Categories:",
    ]
    for category, count in summary["categories"].items():
        lines.append(f"  - {category}: {count}")
    check_bits = ", ".join(f"{key}={value}" for key, value in summary["observed_check_status"].items())
    lines.append(f"Observed check statuses: {check_bits or 'none'}")
    lines.append(f"Expectation cases: {summary['expectation_cases']}")
    totals = summary["totals"]
    lines.append(
        "Corpus size: "
        f"{totals['changed_files']} changed files, "
        f"+{totals['additions']}/-{totals['deletions']} lines"
    )
    return "\n".join(lines)
