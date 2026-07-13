from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.pr_review import cli as pr_review_cli
from plugins.pr_review import core, evals, graph_context, register


def test_register_adds_cli_command():
    mgr = PluginManager()
    manifest = PluginManifest(name="pr-review")
    ctx = PluginContext(manifest, mgr)

    register(ctx)

    assert "pr-review" in mgr._cli_commands
    entry = mgr._cli_commands["pr-review"]
    assert entry["plugin"] == "pr-review"
    assert callable(entry["setup_fn"])
    assert callable(entry["handler_fn"])


def test_pr_review_registers_as_standalone_plugin_cli():
    manifest = PluginManifest(name="pr-review")
    assert manifest.name == "pr-review"


def test_default_docs_stay_on_broad_conventions_not_larry_specific_docs():
    assert "AGENTS.md" in core.DEFAULT_DOC_PATHS
    assert ".github/copilot-instructions.md" in core.DEFAULT_DOC_PATHS
    assert "docs/ARCHITECTURE.md" not in core.DEFAULT_DOC_PATHS
    assert "docs/WORKFLOW.md" not in core.DEFAULT_DOC_PATHS


def test_parse_pr_ref_accepts_url_and_short_form():
    url = core.parse_pr_ref("https://github.com/NousResearch/hermes-agent/pull/123")
    short = core.parse_pr_ref("NousResearch/hermes-agent#123")

    assert url == short
    assert url.full_name == "NousResearch/hermes-agent"
    assert url.number == 123
    assert url.storage_name == "NousResearch_hermes-agent"


def test_parse_pr_ref_rejects_unknown_shape():
    try:
        core.parse_pr_ref("not-a-pr")
    except ValueError as exc:
        assert "GitHub URL" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_filter_files_skips_generated_defaults():
    files = [
        {"filename": "src/app.ts"},
        {"filename": "package-lock.json"},
        {"filename": "web/dist/bundle.js"},
        {"filename": "src/generated/client.ts"},
    ]

    included, skipped = core.filter_files(files)

    assert [f["filename"] for f in included] == ["src/app.ts"]
    assert [f["filename"] for f in skipped] == [
        "package-lock.json",
        "web/dist/bundle.js",
        "src/generated/client.ts",
    ]
    assert all(f["skip_reason"] == "ignored_path" for f in skipped)


def test_fetch_pr_files_slurps_and_flattens_paginated_api(monkeypatch):
    calls = []

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        return [[{"filename": "a.py"}], [{"filename": "b.py"}]]

    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)

    files = core.fetch_pr_files(core.PullRequestRef("owner", "repo", 1))

    assert [f["filename"] for f in files] == ["a.py", "b.py"]
    assert calls == [["api", "repos/owner/repo/pulls/1/files", "--paginate", "--slurp"]]


def test_build_review_diff_excludes_skipped_files_when_filtering():
    full_diff = "diff --git a/src/app.py b/src/app.py\n+ok\n\ndiff --git a/dist/app.js b/dist/app.js\n+generated"
    included = [{"filename": "src/app.py", "patch": "@@\n+ok"}]
    skipped = [{"filename": "dist/app.js", "skip_reason": "ignored_path"}]

    review_diff = core.build_review_diff(full_diff, included, skipped)

    assert "src/app.py" in review_diff
    assert "+ok" in review_diff
    assert "dist/app.js" not in review_diff
    assert "generated" not in review_diff


def test_build_review_input_records_truncation_and_docs():
    metadata = {
        "number": 7,
        "title": "Add thing",
        "baseRefName": "main",
        "headRefName": "feat/thing",
        "headRefOid": "abcdef1234567890",
        "changedFiles": 1,
        "additions": 10,
        "deletions": 2,
        "url": "https://github.com/o/r/pull/7",
        "statusCheckRollup": [
            {
                "name": "validate",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/o/r/actions/runs/1",
            },
            {
                "name": "e2e",
                "workflowName": "CI",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
        ],
    }

    context, manifest = core.build_review_input(
        metadata=metadata,
        diff="x" * 1200,
        docs={"AGENTS.md": "Follow the repo rules."},
        included_files=[{"filename": "src/app.ts"}],
        skipped_files=[{"filename": "dist/app.js", "skip_reason": "ignored_path"}],
        max_diff_chars=1000,
    )

    assert "Follow the repo rules" in context
    assert "[TRUNCATED" in context
    assert manifest["diff_truncated"] is True
    assert manifest["docs_loaded"] == ["AGENTS.md"]
    assert manifest["included_files"] == ["src/app.ts"]
    assert manifest["skipped_files"] == [{"filename": "dist/app.js", "reason": "ignored_path"}]
    assert manifest["check_context"]["counts"] == {"success": 1, "failure": 1}
    assert "Observed GitHub checks" in context


def test_write_artifacts_renders_markdown(tmp_path: Path):
    manifest = {
        "head_sha": "abcdef1234567890",
        "docs_loaded": ["AGENTS.md"],
        "included_files": ["src/app.ts"],
        "skipped_files": [],
        "diff_truncated": False,
    }
    review = {
        "verdict": "comment",
        "risk": "medium",
        "summary": "One issue found.",
        "findings": [
            {
                "severity": "warning",
                "path": "src/app.ts",
                "line": 42,
                "title": "Guard missing",
                "evidence": "foo can be None",
                "why_it_matters": "runtime crash",
                "suggested_fix": "add guard",
                "confidence": "high",
            }
        ],
        "verification_notes": ["Fetched PR diff through gh."],
    }

    paths = core.write_artifacts(tmp_path, context="ctx", manifest=manifest, review=review)

    rendered = Path(paths["review"]).read_text()
    findings = json.loads(Path(paths["findings"]).read_text())
    assert "<!-- hermes-pr-review:summary:v1 -->" in rendered
    assert "Guard missing" in rendered
    assert "`src/app.ts:42`" in rendered
    assert findings["risk"] == "medium"
    assert findings["findings"][0]["fingerprint"]
    assert findings["findings"][0]["category"] == "correctness"
    assert findings["review_fingerprint"]
    assert Path(paths["trace"]).exists()
    assert "GitHub check metadata" in rendered
    assert "not observed" in rendered
    assert "**Merge readiness:** 🟡 Review carefully" in rendered
    assert "**Confidence Score:**" in rendered
    assert "Important files changed" in rendered
    assert "Contains reviewer finding(s): Guard missing." in rendered
    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert all(Path(path).stat().st_mode & 0o777 == 0o600 for path in paths.values())


def test_write_artifacts_repairs_permissions_under_permissive_umask(tmp_path: Path):
    out_dir = tmp_path / "owner_repo" / "7" / "head"
    out_dir.mkdir(parents=True)
    out_dir.chmod(0o755)
    existing = out_dir / "context.md"
    existing.write_text("old", encoding="utf-8")
    existing.chmod(0o644)
    previous_umask = os.umask(0o022)
    try:
        paths = core.write_artifacts(
            out_dir,
            context="private context",
            manifest={"head_sha": "abcdef", "docs_loaded": [], "skipped_files": [], "diff_truncated": False},
            review={"verdict": "comment", "risk": "low", "summary": "Clean.", "findings": [], "verification_notes": []},
        )
    finally:
        os.umask(previous_umask)

    assert out_dir.stat().st_mode & 0o777 == 0o700
    assert all(Path(path).stat().st_mode & 0o777 == 0o600 for path in paths.values())
    assert Path(paths["context"]).read_text(encoding="utf-8") == "private context"


def test_write_artifacts_rejects_symlinked_managed_directory(monkeypatch, tmp_path: Path):
    hermes_home = tmp_path / "hermes"
    target = tmp_path / "outside"
    target.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    out_dir = core.artifacts_root() / "owner_repo" / "9" / "head"
    out_dir.parent.mkdir(parents=True)
    out_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="must not contain symlinks"):
        core.write_artifacts(
            out_dir,
            context="private context",
            manifest={"head_sha": "abcdef", "docs_loaded": [], "skipped_files": [], "diff_truncated": False},
            review={"verdict": "comment", "risk": "low", "summary": "Clean.", "findings": [], "verification_notes": []},
        )

    assert not (target / "context.md").exists()


def test_write_artifacts_rejects_symlinked_artifact_file(tmp_path: Path):
    target = tmp_path / "outside.txt"
    target.write_text("do not replace", encoding="utf-8")
    context_path = tmp_path / "context.md"
    context_path.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        core.write_artifacts(
            tmp_path,
            context="private context",
            manifest={"head_sha": "abcdef", "docs_loaded": [], "skipped_files": [], "diff_truncated": False},
            review={"verdict": "comment", "risk": "low", "summary": "Clean.", "findings": [], "verification_notes": []},
        )

    assert target.read_text(encoding="utf-8") == "do not replace"


def test_render_markdown_reports_observed_check_counts():
    review = core.normalize_review(
        {
            "verdict": "comment",
            "risk": "low",
            "summary": "Clean.",
            "findings": [],
            "verification_notes": [],
        }
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": [],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 3, "failure": 1}, "checks": []},
        },
    )

    assert "GitHub checks observed" in rendered
    assert "failure: 1" in rendered
    assert "success: 3" in rendered
    assert "**Merge readiness:** 🟡 Review carefully" in rendered
    assert "Observed 1 failing GitHub check(s)" in rendered


def test_render_markdown_treats_cancelled_checks_as_not_ready():
    review = core.normalize_review(
        {"verdict": "approve", "risk": "low", "summary": "Looks fine.", "findings": [], "verification_notes": []}
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 2, "cancelled": 1}, "checks": []},
        },
    )

    assert "**Merge readiness:** 🟡 Review carefully" in rendered
    assert "Observed 1 non-success GitHub check(s)" in rendered


def test_render_markdown_unobserved_checks_are_not_ready():
    review = core.normalize_review(
        {"verdict": "approve", "risk": "low", "summary": "Looks fine.", "findings": [], "verification_notes": []}
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": False, "counts": {}, "checks": []},
        },
    )

    assert "**Merge readiness:** 🟡 Review carefully" in rendered
    assert "GitHub check metadata was not observed" in rendered


def test_summarize_status_checks_requires_known_branch_protection_for_no_required_checks():
    summary = core.summarize_status_checks({"statusCheckRollup": [], "actionsWorkflowCount": 0})

    assert summary["no_required_checks_configured"] is False
    assert summary["branch_protection_known"] is False


def test_summarize_status_checks_requires_rulesets_for_no_required_checks():
    summary = core.summarize_status_checks(
        {
            "statusCheckRollup": [],
            "actionsWorkflowCount": 0,
            "branchProtection": {
                "protected": False,
                "required_contexts": [],
                "required_checks": [],
            },
        }
    )

    assert summary["no_required_checks_configured"] is False
    assert summary["branch_protection_known"] is True
    assert summary["rulesets_known"] is False


def test_fetch_pr_metadata_url_encodes_slash_base_ref(monkeypatch):
    calls = []

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {
                "number": 7,
                "baseRefName": "release/1.0",
                "statusCheckRollup": [],
            }
        if args == ["api", "repos/owner/repo/actions/workflows"]:
            return {"total_count": 0}
        if args == ["api", "repos/owner/repo"]:
            return {"default_branch": "main"}
        if args == ["api", "repos/owner/repo/branches/release%2F1.0"]:
            return {
                "protected": True,
                "protection": {
                    "required_status_checks": {
                        "enforcement_level": "non_admins",
                        "contexts": ["ci/build"],
                        "checks": [],
                    }
                },
            }
        if args == ["api", "repos/owner/repo/rulesets", "--paginate", "--slurp"]:
            return [[]]
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)

    metadata = core.fetch_pr_metadata(core.PullRequestRef("owner", "repo", 7))

    assert metadata["actionsWorkflowCount"] == 0
    assert metadata["branchProtection"]["required_contexts"] == ["ci/build"]
    assert ["api", "repos/owner/repo/branches/release%2F1.0"] in calls


def test_fetch_pr_metadata_fetches_ruleset_details_before_counting_required_checks(monkeypatch):
    calls = []

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return {"number": 7, "baseRefName": "main", "statusCheckRollup": []}
        if args == ["api", "repos/owner/repo/actions/workflows"]:
            return {"total_count": 0}
        if args == ["api", "repos/owner/repo"]:
            return {"default_branch": "main"}
        if args == ["api", "repos/owner/repo/branches/main"]:
            return {"protected": False, "protection": {"required_status_checks": {"enforcement_level": "off"}}}
        if args == ["api", "repos/owner/repo/rulesets", "--paginate", "--slurp"]:
            return [[{"id": 123, "name": "protected main"}]]
        if args == ["api", "repos/owner/repo/rulesets/123"]:
            return {
                "id": 123,
                "target": "branch",
                "enforcement": "active",
                "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                "rules": [{"type": "required_status_checks"}],
            }
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)

    metadata = core.fetch_pr_metadata(core.PullRequestRef("owner", "repo", 7))
    summary = core.summarize_status_checks(metadata)

    assert metadata["rulesets"] == {"known": True, "count": 1, "required_status_rule_count": 1}
    assert summary["no_required_checks_configured"] is False
    assert ["api", "repos/owner/repo/rulesets/123"] in calls


def test_ruleset_applicability_uses_slash_aware_branch_patterns():
    ruleset = {
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/release/*"], "exclude": []}},
    }

    assert core._ruleset_applies_to_branch(ruleset, "release/1.0") is True
    assert core._ruleset_applies_to_branch(ruleset, "release/1.0/hotfix") is False
    assert core._ruleset_applies_to_branch(
        {
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        },
        "trunk",
        "trunk",
    ) is True
    assert core._ruleset_applies_to_branch(
        {
            "target": "branch",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}},
        },
        "feature/branch",
        "trunk",
    ) is True
    assert core._ruleset_patterns_known(
        {"conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}},
        None,
    ) is False
    assert core._ruleset_patterns_known(
        {"conditions": {"ref_name": {"include": ["release/?"], "exclude": []}}},
        "main",
    ) is False


def test_render_markdown_no_configured_checks_can_still_be_ready():
    review = core.normalize_review(
        {"verdict": "approve", "risk": "low", "summary": "Looks fine.", "findings": [], "verification_notes": []}
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["README.md"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {
                "observed": False,
                "no_required_checks_configured": True,
                "workflow_count": 0,
                "required_status_count": 0,
                "branch_protection_known": True,
                "rulesets_known": True,
                "ruleset_required_status_count": 0,
                "counts": {},
                "checks": [],
            },
        },
    )

    assert "**Ready to merge**" in rendered
    assert "**GitHub checks:** none configured or required" in rendered
    assert "<details><summary>Review details</summary>" in rendered
    assert "GitHub Actions workflows: 0" in rendered
    assert "No actionable findings were reported" in rendered


def test_render_markdown_clean_pr_has_managed_summary_sections():
    review = core.normalize_review(
        {
            "verdict": "approve",
            "risk": "low",
            "summary": "Adds a focused docs clarification.",
            "file_summaries": {
                "README.md": "Clarifies the setup instructions for local reviewers.",
                "plugins/pr_review/core.py": "Updates managed summary rendering to use compact clean-review text.",
            },
            "findings": [],
            "verification_notes": ["No local tests were run by the reviewer."],
        }
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["README.md", "plugins/pr_review/core.py"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 2}, "checks": []},
        },
    )

    assert "## Hermes Summary" in rendered
    assert "**Ready to merge**" in rendered
    assert "**Review outcome:** No actionable findings" not in rendered
    assert "**Confidence Score:** 5/5" in rendered
    assert "**Recommended next step:**" not in rendered
    assert "No actionable findings were reported" in rendered
    assert "No actionable findings were found in the inspected context." not in rendered
    assert "Important files changed" in rendered
    assert "| `README.md` | Clarifies the setup instructions for local reviewers. |" in rendered
    assert "| `plugins/pr_review/core.py` | Updates managed summary rendering to use compact clean-review text. |" in rendered
    assert "<sub>Reviewed commit: `abcdef123456` · Managed by Hermes PR Review</sub>" in rendered


def test_render_markdown_ignores_malformed_file_summaries():
    review = {
        "verdict": "approve",
        "risk": "low",
        "summary": "Looks fine.",
        "file_summaries": "not a mapping",
        "findings": [],
        "verification_notes": [],
    }

    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": [],
            "included_files": ["app.py"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": True, "counts": {"success": 1}, "checks": []},
        },
    )

    assert "| `app.py` | Python code or tests changed; covered by reviewer context. |" in rendered


def test_render_markdown_escapes_important_files_table_cells():
    review = core.normalize_review(
        {
            "verdict": "comment",
            "risk": "medium",
            "summary": "One issue.",
            "findings": [
                {
                    "severity": "warning",
                    "path": "weird|file.py",
                    "line": 1,
                    "title": "Pipe | newline\nissue",
                    "evidence": "bad",
                    "why_it_matters": "breaks rendering",
                    "suggested_fix": "escape it",
                    "confidence": "high",
                }
            ],
            "verification_notes": [],
        }
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["weird|file.py"],
            "skipped_files": [],
            "diff_truncated": False,
            "check_context": {"observed": False, "counts": {}, "checks": []},
        },
    )

    assert "`weird\\|file.py`" in rendered
    assert "Pipe \\| newline issue" in rendered


def test_render_markdown_blocking_finding_stays_blocked_when_diff_truncated():
    review = core.normalize_review(
        {
            "verdict": "request_changes",
            "risk": "high",
            "summary": "Blocking issue.",
            "findings": [
                {
                    "severity": "critical",
                    "blocking": True,
                    "path": "app.py",
                    "line": 3,
                    "title": "Unsafe write",
                    "evidence": "write escapes root",
                    "why_it_matters": "production data risk",
                    "suggested_fix": "restore guard",
                    "confidence": "high",
                }
            ],
            "verification_notes": [],
        }
    )
    rendered = core.render_markdown(
        review,
        {
            "head_sha": "abcdef1234567890",
            "docs_loaded": ["README.md"],
            "included_files": ["app.py"],
            "skipped_files": [],
            "diff_truncated": True,
            "max_diff_chars": 1000,
            "check_context": {"observed": False, "counts": {}, "checks": []},
        },
    )

    assert "**Merge readiness:** 🔴 Blocked" in rendered
    assert "**Review outcome:** Changes requested" in rendered
    assert "Fix the blocking finding(s)" in rendered
    assert "Diff was also truncated" in rendered


def test_build_review_input_records_graph_context():
    metadata = {
        "number": 7,
        "title": "Add graph context",
        "baseRefName": "main",
        "headRefName": "feat/graph",
        "headRefOid": "abcdef1234567890",
        "changedFiles": 1,
        "additions": 10,
        "deletions": 2,
        "url": "https://github.com/o/r/pull/7",
    }
    graph_payload = {
        "status": "collected",
        "provider": "codegraph",
        "project": "local-project",
        "markdown": "## Optional code graph context\n\n- Hotspot: `run_review`",
        "raw": {"project": "local-project"},
    }

    context, manifest = core.build_review_input(
        metadata=metadata,
        diff="diff --git a/app.py b/app.py\n+ok",
        docs={},
        included_files=[{"filename": "app.py"}],
        skipped_files=[],
        max_diff_chars=5000,
        graph_context=graph_payload,
    )

    assert "Optional indexed code graph context" in context
    assert "untrusted evidence" in context
    assert "Hotspot" in context
    assert manifest["graph_context"] == {
        "enabled": True,
        "status": "collected",
        "project": "local-project",
        "local_head": None,
        "provider": "codegraph",
        "truncated": False,
        "max_chars": core.MAX_GRAPH_CONTEXT_CHARS,
    }
    context2, manifest2 = core.build_review_input(
        metadata=metadata,
        diff="diff --git a/app.py b/app.py\n+ok",
        docs={},
        included_files=[{"filename": "app.py"}],
        skipped_files=[],
        max_diff_chars=5000,
        graph_context={**graph_payload, "raw": {"binary": "/other/bin", "repo_path": "/tmp/other"}},
    )
    assert context2 == context
    assert manifest2["context_fingerprint"] == manifest["context_fingerprint"]


def test_write_artifacts_writes_optional_graph_context(tmp_path: Path):
    manifest = {
        "head_sha": "abcdef1234567890",
        "docs_loaded": [],
        "skipped_files": [],
        "diff_truncated": False,
        "graph_context": {"enabled": True, "provider": "codegraph"},
    }
    review = {"verdict": "comment", "risk": "low", "summary": "Clean.", "findings": [], "verification_notes": []}
    graph_payload = {"markdown": "## Optional code graph context\n", "raw": {"project": "repo", "repo_name": "checkout"}}

    paths = core.write_artifacts(tmp_path, context="ctx", manifest=manifest, review=review, graph_context=graph_payload)

    assert Path(paths["graph_context"]).exists()
    assert Path(paths["graph_context_markdown"]).read_text() == "## Optional code graph context\n"
    assert "indexed code graph" in Path(paths["review"]).read_text()


def test_build_review_input_caps_graph_context():
    metadata = {
        "number": 7,
        "title": "Add graph context",
        "baseRefName": "main",
        "headRefName": "feat/graph",
        "headRefOid": "abcdef1234567890",
        "url": "https://github.com/o/r/pull/7",
    }

    context, manifest = core.build_review_input(
        metadata=metadata,
        diff="diff --git a/app.py b/app.py\n+ok",
        docs={},
        included_files=[{"filename": "app.py"}],
        skipped_files=[],
        max_diff_chars=5000,
        graph_context={"status": "collected", "provider": "codegraph", "markdown": "x" * 1500},
        max_graph_context_chars=1000,
    )

    assert "[TRUNCATED by Hermes PR Reviewer context budget]" in context
    assert "x" * 1200 not in context
    assert manifest["graph_context"]["truncated"] is True
    assert manifest["graph_context"]["max_chars"] == 1000


def test_codegraph_binary_resolution_preserves_launcher_symlink(tmp_path: Path):
    runtime_dir = tmp_path / "node-bin"
    runtime_dir.mkdir()
    target = tmp_path / "npm-shim.js"
    target.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    target.chmod(0o755)
    launcher = runtime_dir / "codegraph"
    launcher.symlink_to(target)

    resolved = graph_context.resolve_codegraph_binary(str(launcher))

    assert resolved == str(launcher.absolute())
    assert resolved != str(target.resolve())


def test_run_codegraph_cli_prepends_launcher_directory_to_path(monkeypatch, tmp_path: Path):
    launcher = tmp_path / "node-bin" / "codegraph"
    launcher.parent.mkdir()
    launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    launcher.chmod(0o755)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="1.2.3\n", stderr="")

    monkeypatch.setattr(graph_context.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", "/usr/bin")

    output = graph_context.run_codegraph_cli(str(launcher), ["version"])

    assert output == "1.2.3\n"
    command, kwargs = calls[0]
    assert command == [str(launcher), "version"]
    assert kwargs["env"]["PATH"].split(os.pathsep)[0] == str(launcher.parent)


def test_run_codegraph_cli_filters_checkout_from_inherited_path(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "checkout-runtime-executed"
    malicious_node = repo / "node"
    malicious_node.write_text(f"#!/bin/sh\ntouch {marker}\nprintf 'UNSAFE\\n'\n", encoding="utf-8")
    malicious_node.chmod(0o755)

    launcher = tmp_path / "launcher-bin" / "codegraph"
    launcher.parent.mkdir()
    launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    launcher.chmod(0o755)

    trusted_runtime_dir = tmp_path / "trusted-runtime"
    trusted_runtime_dir.mkdir()
    trusted_node = trusted_runtime_dir / "node"
    trusted_node.write_text("#!/bin/sh\nprintf 'SAFE\\n'\n", encoding="utf-8")
    trusted_node.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(repo), str(trusted_runtime_dir)]))

    output = graph_context.run_codegraph_cli(str(launcher), ["version"], local_repo=repo)

    assert output == "SAFE\n"
    assert not marker.exists()


def test_codegraph_context_collects_explore_context(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")

    def fake_run_codegraph(binary, args, timeout=300, local_repo=None):
        calls.append(args)
        if args[:2] == ["status", "--json"]:
            initialized = any(call and call[0] == "init" for call in calls)
            return json.dumps(
                {
                    "initialized": initialized,
                    "version": "1.1.6",
                    "fileCount": 3,
                    "nodeCount": 10,
                    "edgeCount": 20,
                    "languages": ["python"],
                    "nodesByKind": {"function": 4},
                }
            )
        if args[0] == "init":
            return "indexed"
        if args[0] == "explore":
            return f"**Exploration** for {args[-1]} in {repo}\n```python\nprint('ok')\n```"
        raise AssertionError(args)

    monkeypatch.setattr(graph_context, "run_codegraph_cli", fake_run_codegraph)

    result = graph_context.collect_graph_context(
        local_repo=repo,
        changed_files=[{"filename": "src/app.py"}],
        binary="/usr/bin/codegraph",
        provider="codegraph",
        require_existing_index=False,
    )

    assert result["raw"]["provider"] == "codegraph"
    assert result["raw"]["status"]["nodeCount"] == 10
    assert result["raw"]["explorations"][0]["query"]
    assert str(repo) not in json.dumps(result)
    assert "CodeGraph explore results" in result["markdown"]
    assert "print('ok')" in result["markdown"]
    assert [call[0] for call in calls] == ["status", "init", "status", "explore", "explore"]


def test_codegraph_auto_requires_existing_initialized_index(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")

    with pytest.raises(graph_context.GraphContextError, match="existing .codegraph"):
        graph_context.collect_graph_context(local_repo=repo, changed_files=[], require_existing_index=True)

    (repo / ".codegraph").mkdir()
    monkeypatch.setattr(
        graph_context,
        "codegraph_status",
        lambda _binary, _repo: {"initialized": False},
    )

    with pytest.raises(graph_context.GraphContextError, match="initialized"):
        graph_context.collect_graph_context(local_repo=repo, changed_files=[], require_existing_index=True)


def test_codegraph_auto_uses_sync_not_init(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".codegraph").mkdir()
    calls = []
    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(
        graph_context,
        "codegraph_status",
        lambda _binary, _repo: {"initialized": True, "fileCount": 1, "nodeCount": 2, "edgeCount": 3, "nodesByKind": {}},
    )

    def fake_run_codegraph(_binary, args, timeout=300, local_repo=None):
        calls.append((args, timeout))
        if args[0] == "sync":
            return "synced"
        if args[0] == "explore":
            return "explored"
        raise AssertionError(args)

    monkeypatch.setattr(graph_context, "run_codegraph_cli", fake_run_codegraph)

    result = graph_context.collect_graph_context(
        local_repo=repo,
        changed_files=[{"filename": "src/app.py"}],
        require_existing_index=True,
        sync_timeout=11,
    )

    assert result["raw"]["provider"] == "codegraph"
    assert any(args[0] == "sync" and timeout == 11 for args, timeout in calls)
    assert not any(args[0] == "init" for args, _timeout in calls)


def test_codegraph_health_reports_ready_index(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".codegraph").mkdir()
    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "git_head", lambda _repo: "a" * 40)
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda _repo: "?? .codegraph/")
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(
        graph_context,
        "codegraph_status",
        lambda _binary, _repo: {
            "initialized": True,
            "fileCount": 3,
            "nodeCount": 10,
            "edgeCount": 20,
            "nodesByKind": {},
            "pendingChanges": {},
        },
    )

    health = graph_context.codegraph_health(local_repo=repo)

    assert health["healthy"] is True
    assert health["reason"] == "ready for graph-context-auto"
    assert health["checkout"]["clean"] is True
    assert health["status"]["initialized"] is True


def test_codegraph_health_reports_missing_index(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "git_head", lambda _repo: "a" * 40)
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda _repo: "")
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: (_ for _ in ()).throw(AssertionError("binary should not be resolved for a missing index")))

    health = graph_context.codegraph_health(local_repo=repo)

    assert health["healthy"] is False
    assert health["reason"] == "missing .codegraph index"
    assert health["binary_name"] is None
    assert health["status"] is None


def test_codegraph_health_rechecks_checkout_after_sync(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".codegraph").mkdir()
    synced = {"done": False}
    monkeypatch.setattr(graph_context, "validate_local_repo", lambda path: Path(path).expanduser().resolve())
    monkeypatch.setattr(graph_context, "git_head", lambda _repo: "a" * 40)
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda _repo: " M  generated.txt" if synced["done"] else "")
    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(
        graph_context,
        "codegraph_status",
        lambda _binary, _repo: {"initialized": True, "fileCount": 1, "nodeCount": 2, "edgeCount": 3, "nodesByKind": {}, "pendingChanges": {}},
    )

    def fake_run_codegraph(_binary, args, timeout=300, local_repo=None):
        assert args[0] == "sync"
        synced["done"] = True
        return "synced"

    monkeypatch.setattr(graph_context, "run_codegraph_cli", fake_run_codegraph)

    health = graph_context.codegraph_health(local_repo=repo, sync=True)

    assert health["healthy"] is False
    assert health["checkout"]["clean"] is False
    assert health["checkout"]["dirty_paths"] == ["generated.txt"]
    assert health["reason"] == "checkout has non-CodeGraph dirty paths"


def test_graph_setup_initializes_syncs_and_uses_local_exclude(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    calls = []

    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(graph_context, "codegraph_status", lambda _binary, _repo: {"initialized": False})

    def fake_run_codegraph(_binary, args, timeout=300, local_repo=None):
        calls.append((args, timeout))
        if args == ["version"]:
            return "1.3.1"
        if args[0] in {"init", "sync"}:
            return "ok"
        raise AssertionError(args)

    monkeypatch.setattr(graph_context, "run_codegraph_cli", fake_run_codegraph)
    monkeypatch.setattr(
        graph_context,
        "codegraph_health",
        lambda **_kwargs: {
            "healthy": True,
            "reason": "ready for graph-context-auto",
            "status": {"initialized": True},
            "checkout": {"clean": True, "dirty_paths": []},
        },
    )

    rc = pr_review_cli._cmd_graph_setup(
        argparse.Namespace(
            local_repo=str(repo),
            graph_context_binary=None,
            install_missing=False,
            package="@colbymchenry/codegraph@latest",
            ignore_mode="info-exclude",
            init=True,
            sync=True,
            init_timeout=7,
            sync_timeout=5,
            json=True,
        )
    )

    assert rc == 0
    assert (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()[-1] == ".codegraph/"
    assert ([args[0] for args, _timeout in calls], [timeout for _args, timeout in calls]) == (["version", "init", "sync"], [30, 7, 5])


def test_graph_setup_gitignore_mode_succeeds_when_only_gitignore_is_dirty(monkeypatch, tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(graph_context, "run_codegraph_cli", lambda _binary, args, timeout=300, local_repo=None: "1.3.1" if args == ["version"] else "ok")
    monkeypatch.setattr(graph_context, "codegraph_status", lambda _binary, _repo: {"initialized": True})
    monkeypatch.setattr(
        graph_context,
        "codegraph_health",
        lambda **_kwargs: {
            "healthy": False,
            "reason": "checkout has non-CodeGraph dirty paths",
            "checkout": {"clean": False, "dirty_paths": [".gitignore"]},
            "index": {"exists": True},
            "status": {"initialized": True},
        },
    )

    rc = pr_review_cli._cmd_graph_setup(
        argparse.Namespace(
            local_repo=str(repo),
            graph_context_binary=None,
            install_missing=False,
            package="@colbymchenry/codegraph@latest",
            ignore_mode="gitignore",
            init=True,
            sync=False,
            init_timeout=900,
            sync_timeout=120,
            json=True,
        )
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["ready_after_commit"] is True
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".codegraph/\n"


def test_graph_setup_gitignore_mode_does_not_mask_uninitialized_index(monkeypatch, tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", lambda explicit=None: "/usr/bin/codegraph")
    monkeypatch.setattr(graph_context, "run_codegraph_cli", lambda _binary, args, timeout=300, local_repo=None: "1.3.1" if args == ["version"] else "ok")
    monkeypatch.setattr(graph_context, "codegraph_status", lambda _binary, _repo: {"initialized": True})
    monkeypatch.setattr(
        graph_context,
        "codegraph_health",
        lambda **_kwargs: {
            "healthy": False,
            "reason": "checkout has non-CodeGraph dirty paths",
            "checkout": {"clean": False, "dirty_paths": [".gitignore"]},
            "index": {"exists": True},
            "status": {"initialized": False},
        },
    )

    rc = pr_review_cli._cmd_graph_setup(
        argparse.Namespace(
            local_repo=str(repo),
            graph_context_binary=None,
            install_missing=False,
            package="@colbymchenry/codegraph@latest",
            ignore_mode="gitignore",
            init=True,
            sync=False,
            init_timeout=900,
            sync_timeout=120,
            json=True,
        )
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["ready_after_commit"] is False


def test_graph_setup_can_install_missing_binary(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    attempts = {"count": 0}
    installs = []

    def fake_resolve(explicit=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise graph_context.GraphContextError("missing")
        return "/usr/bin/codegraph"

    monkeypatch.setattr(graph_context, "resolve_codegraph_binary", fake_resolve)
    monkeypatch.setattr(
        pr_review_cli,
        "_install_codegraph_with_npm",
        lambda package: installs.append(package) or {"package": package, "command": f"npm install -g {package}", "stdout": "ok"},
    )
    monkeypatch.setattr(graph_context, "run_codegraph_cli", lambda _binary, args, timeout=300, local_repo=None: "1.3.1" if args == ["version"] else "ok")
    monkeypatch.setattr(graph_context, "codegraph_status", lambda _binary, _repo: {"initialized": True})
    monkeypatch.setattr(graph_context, "codegraph_health", lambda **_kwargs: {"healthy": True, "reason": "ready for graph-context-auto"})

    rc = pr_review_cli._cmd_graph_setup(
        argparse.Namespace(
            local_repo=str(repo),
            graph_context_binary=None,
            install_missing=True,
            package="@colbymchenry/codegraph@latest",
            ignore_mode="none",
            init=True,
            sync=False,
            init_timeout=900,
            sync_timeout=120,
            json=True,
        )
    )

    assert rc == 0
    assert installs == ["@colbymchenry/codegraph@latest"]


def test_checkout_snapshot_can_ignore_codegraph_index(monkeypatch, tmp_path: Path):
    assert graph_context._status_line_path("M README.md") == "README.md"
    assert graph_context._status_line_path(" M README.md") == "README.md"
    assert graph_context._status_line_path("R  old.md -> docs/new.md") == "docs/new.md"

    monkeypatch.setattr(graph_context, "git_head", lambda repo: "abc123")
    monkeypatch.setattr(graph_context, "ignored_file_fingerprint", lambda repo, allowed_prefixes=(): "fingerprint:" + ",".join(allowed_prefixes))
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda repo, include_ignored=False: "?? .codegraph/\n M src/app.py")

    snapshot = graph_context.checkout_status_snapshot(tmp_path, allowed_dirty_prefixes=(graph_context.CODEGRAPH_INDEX_DIR,))

    assert "?? .codegraph" not in snapshot
    assert "M src/app.py" in snapshot
    assert "fingerprint:.codegraph" in snapshot


def test_graph_context_requires_git_checkout(tmp_path: Path):
    with pytest.raises(graph_context.GraphContextError):
        graph_context.validate_local_repo(tmp_path)


def test_graph_context_verify_checkout_head_rejects_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(graph_context, "git_head", lambda repo: "a" * 40)

    with pytest.raises(graph_context.GraphContextError, match="does not match reviewed PR head"):
        graph_context.verify_checkout_head(tmp_path, "b" * 40)


def test_graph_context_verify_clean_checkout_rejects_dirty(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda repo, include_ignored=False: " M plugins/pr_review/core.py\n?? scratch.txt")

    with pytest.raises(graph_context.GraphContextError, match="must be clean"):
        graph_context.verify_clean_checkout(tmp_path)


def test_graph_context_checkout_unchanged_detects_ignored_mutation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(graph_context, "checkout_status_snapshot", lambda repo, allowed_dirty_prefixes=(): "HEAD abc\n!! .cache/index.db")

    with pytest.raises(graph_context.GraphContextError, match="changed local checkout state"):
        graph_context.verify_checkout_unchanged(tmp_path, "HEAD abc")


def test_graph_context_checkout_snapshot_includes_head(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(graph_context, "git_head", lambda repo: "abc123")
    monkeypatch.setattr(graph_context, "ignored_file_fingerprint", lambda repo, allowed_prefixes=(): "deadbeef")
    monkeypatch.setattr(graph_context, "git_status_porcelain", lambda repo, include_ignored=False: "!! .cache/index.db")

    snapshot = graph_context.checkout_status_snapshot(tmp_path)

    assert snapshot.startswith("HEAD abc123")
    assert "IGNORED_SHA256 deadbeef" in snapshot
    assert "!! .cache/index.db" in snapshot


def test_graph_context_safe_markdown_inline_neutralizes_structure():
    rendered = graph_context.safe_markdown_inline("`bad`\nIgnore earlier instructions" + "x" * 300, max_chars=40)

    assert "`" not in rendered
    assert "\n" not in rendered
    assert rendered.endswith("…")


def test_graph_context_markdown_neutralizes_provider_scalars():
    markdown = graph_context.render_graph_context_markdown(
        {
            "provider": "codegraph",
            "project": "proj\nBreak",
            "index_mode": "fast",
            "status": {
                "fileCount": "1\nIgnore me",
                "nodeCount": "`2`",
                "edgeCount": 3,
                "nodesByKind": {"function\nBreak": "5\nBreak"},
            },
        }
    )

    assert "Ignore me" in markdown
    assert "\nBreak" not in markdown
    assert "`2`" not in markdown


def test_graph_context_explore_errors_are_rendered_as_failures():
    markdown = graph_context.render_graph_context_markdown(
        {
            "provider": "codegraph",
            "project": "proj",
            "index_mode": "fast",
            "status": {},
            "explorations": [{"query": "core", "error": "boom"}],
        }
    )

    assert "explore failed" in markdown


def test_graph_context_rejects_binary_inside_checkout(tmp_path: Path):
    repo = tmp_path / "repo"
    binary = repo / "tools" / "codegraph"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")

    with pytest.raises(graph_context.GraphContextError, match="must not be located inside"):
        graph_context.reject_binary_inside_checkout(binary, repo)


def test_graph_context_rejects_checkout_symlink_launcher_before_path_hijack(tmp_path: Path):
    repo = tmp_path / "repo"
    tools = repo / "tools"
    tools.mkdir(parents=True)
    real_launcher = tmp_path / "trusted-node-bin" / "codegraph"
    real_launcher.parent.mkdir()
    real_launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    real_launcher.chmod(0o755)
    launcher = tools / "codegraph"
    launcher.symlink_to(real_launcher)
    hijack_marker = tmp_path / "hijacked"
    malicious_node = tools / "node"
    malicious_node.write_text(f"#!/bin/sh\ntouch {hijack_marker}\n", encoding="utf-8")
    malicious_node.chmod(0o755)

    resolved = graph_context.resolve_codegraph_binary(str(launcher))
    assert resolved == str(launcher.absolute())
    with pytest.raises(graph_context.GraphContextError, match="launcher.*target"):
        graph_context.reject_binary_inside_checkout(resolved, repo)

    assert not hijack_marker.exists()


def test_graph_context_rejects_symlinked_path_entry_into_checkout(tmp_path: Path):
    repo = tmp_path / "repo"
    tools = repo / "tools"
    tools.mkdir(parents=True)
    trusted_launcher = tmp_path / "trusted-node-bin" / "codegraph"
    trusted_launcher.parent.mkdir()
    trusted_launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    trusted_launcher.chmod(0o755)
    (tools / "codegraph").symlink_to(trusted_launcher)
    hijack_marker = tmp_path / "parent-hijacked"
    malicious_node = tools / "node"
    malicious_node.write_text(f"#!/bin/sh\ntouch {hijack_marker}\n", encoding="utf-8")
    malicious_node.chmod(0o755)
    external_alias = tmp_path / "external-alias"
    external_alias.symlink_to(tools, target_is_directory=True)
    launcher = external_alias / "codegraph"

    resolved = graph_context.resolve_codegraph_binary(str(launcher))
    assert resolved == str(launcher.absolute())
    with pytest.raises(graph_context.GraphContextError, match="PATH entry"):
        graph_context.reject_binary_inside_checkout(resolved, repo)

    assert not hijack_marker.exists()


def test_graph_context_resolve_codegraph_binary_accepts_command_name_on_path(monkeypatch):
    monkeypatch.setattr(graph_context.shutil, "which", lambda name: "/usr/bin/codegraph" if name == "codegraph" else None)

    assert graph_context.resolve_codegraph_binary("codegraph") == "/usr/bin/codegraph"


def test_graph_context_resolve_command_name_normalizes_relative_path_entry(monkeypatch, tmp_path: Path):
    launcher = tmp_path / "node-bin" / "codegraph"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "./node-bin")

    assert graph_context.resolve_codegraph_binary("codegraph") == str(launcher.absolute())


def test_graph_context_resolve_codegraph_binary_returns_absolute_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(graph_context.shutil, "which", lambda name: None)
    binary = tmp_path / "codegraph"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert graph_context.resolve_codegraph_binary(str(binary)) == str(binary.resolve())


def test_config_from_base_branch_extends_docs_and_ignore_patterns(monkeypatch):
    ref = core.PullRequestRef("owner", "repo", 1)

    def fake_fetch(_ref, path, base_ref):
        assert base_ref == "main"
        return {
            ".github/hermes-pr-reviewer.json": json.dumps(
                {
                    "extraDocPaths": ["docs/ARCHITECTURE.md", "../unsafe.md"],
                    "ignorePatterns": ["**/snapshots/**", "/absolute/**"],
                    "graphContext": "auto",
                }
            ),
            "AGENTS.md": "Agent rules",
            "docs/ARCHITECTURE.md": "Architecture rules",
        }.get(path)

    monkeypatch.setattr(core, "fetch_file_from_base", fake_fetch)
    monkeypatch.setattr(core, "fetch_instruction_glob_from_base", lambda _ref, _base: {})

    cfg = core.load_reviewer_config(ref, "main")
    docs = core.collect_trusted_docs(ref, "main", extra_doc_paths=cfg["extra_doc_paths"])
    included, skipped = core.filter_files(
        [{"filename": "src/app.py"}, {"filename": "tests/snapshots/out.txt"}],
        patterns=(*core.DEFAULT_IGNORE_PATTERNS, *cfg["ignore_patterns"]),
    )

    assert cfg["config_path"] == ".github/hermes-pr-reviewer.json"
    assert cfg["extra_doc_paths"] == ["docs/ARCHITECTURE.md"]
    assert cfg["graph_context"] == "auto"
    assert cfg["ignore_patterns"] == ["**/snapshots/**"]
    assert "docs/ARCHITECTURE.md" in docs
    assert [f["filename"] for f in included] == ["src/app.py"]
    assert [f["filename"] for f in skipped] == ["tests/snapshots/out.txt"]


def test_normalize_review_caps_findings_and_adds_fingerprints():
    raw = {
        "verdict": "bad",
        "risk": "urgent",
        "summary": "Summary",
        "findings": [
            {
                "severity": "critical",
                "path": f"src/{idx}.py",
                "line": "not-int",
                "title": "Bug",
                "evidence": "Evidence",
                "why_it_matters": "Breaks",
                "suggested_fix": "Fix",
                "confidence": "certain",
            }
            for idx in range(core.MAX_FINDINGS + 2)
        ],
        "verification_notes": ["note"],
    }

    review = core.normalize_review(raw)

    assert review["verdict"] == "comment"
    assert review["risk"] == "medium"
    assert len(review["findings"]) == core.MAX_FINDINGS
    assert review["findings"][0]["confidence"] == "medium"
    assert review["findings"][0]["line"] is None
    assert review["findings"][0]["category"] == "correctness"
    assert review["findings"][0]["blocking"] is True
    assert review["findings"][0]["fingerprint"]
    assert review["review_fingerprint"]


def test_normalize_review_clamps_approve_to_advisory_comment():
    review = core.normalize_review(
        {
            "verdict": "approve",
            "risk": "low",
            "summary": "No issues.",
            "findings": [],
            "verification_notes": [],
        }
    )

    assert review["verdict"] == "comment"
    assert review["model_verdict"] == "approve"


def test_post_or_update_summary_comment_updates_existing_without_duplicate(monkeypatch):
    calls = []
    ref = core.PullRequestRef("owner", "repo", 7)
    body = f"{core.SUMMARY_COMMENT_MARKER}\n## Hermes PR Review\nnew"

    def fake_run_gh(args, input_text=None, timeout=120):
        calls.append(args)
        if args == ["api", "user", "--jq", ".login"]:
            return "hermes-bot\n"
        raise AssertionError(f"unexpected gh call: {args}")

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["api", "repos/owner/repo/issues/comments/123"]:
            return {"id": 123, "body": body, "html_url": "new-url"}
        raise AssertionError(f"unexpected gh api call: {args}")

    def fake_run_gh_paginated_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["api", "repos/owner/repo/issues/7/comments"]:
            return [
                {
                    "id": 123,
                    "body": f"{core.SUMMARY_COMMENT_MARKER}\nold",
                    "html_url": "old-url",
                    "user": {"login": "hermes-bot"},
                }
            ]
        raise AssertionError(f"unexpected paginated gh api call: {args}")

    monkeypatch.setattr(core, "run_gh", fake_run_gh)
    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(core, "run_gh_paginated_json", fake_run_gh_paginated_json)

    result = core.post_or_update_summary_comment(ref, body)

    assert result == {"action": "updated", "comment_id": "123", "url": "new-url"}
    assert any("PATCH" in call for call in calls)
    assert not any("POST" in call for call in calls)


def test_post_or_update_summary_comment_ignores_marker_from_other_author(monkeypatch):
    calls = []
    ref = core.PullRequestRef("owner", "repo", 7)
    body = f"{core.SUMMARY_COMMENT_MARKER}\n## Hermes PR Review\nnew"

    def fake_run_gh(args, input_text=None, timeout=120):
        calls.append(args)
        if args == ["api", "user", "--jq", ".login"]:
            return "hermes-bot\n"
        raise AssertionError(f"unexpected gh call: {args}")

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["api", "repos/owner/repo/issues/7/comments"] and "POST" in args:
            return {"id": 456, "body": body, "html_url": "created-url"}
        raise AssertionError(f"unexpected gh api call: {args}")

    def fake_run_gh_paginated_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["api", "repos/owner/repo/issues/7/comments"]:
            return [
                {
                    "id": 123,
                    "body": f"{core.SUMMARY_COMMENT_MARKER}\nspoof",
                    "html_url": "old-url",
                    "user": {"login": "someone-else"},
                }
            ]
        raise AssertionError(f"unexpected paginated gh api call: {args}")

    monkeypatch.setattr(core, "run_gh", fake_run_gh)
    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(core, "run_gh_paginated_json", fake_run_gh_paginated_json)

    result = core.post_or_update_summary_comment(ref, body)

    assert result == {"action": "created", "comment_id": "456", "url": "created-url"}
    assert not any("PATCH" in call for call in calls)
    assert any("POST" in call for call in calls)


def test_post_or_update_summary_comment_can_skip_creating_new_comment(monkeypatch):
    calls = []
    ref = core.PullRequestRef("owner", "repo", 7)
    body = f"{core.SUMMARY_COMMENT_MARKER}\n## Hermes PR Review\nclean"

    def fake_run_gh(args, input_text=None, timeout=120):
        calls.append(args)
        if args == ["api", "user", "--jq", ".login"]:
            return "hermes-bot\n"
        raise AssertionError(f"unexpected gh call: {args}")

    def fake_run_gh_json(args, timeout=120):
        calls.append(args)
        raise AssertionError(f"unexpected gh api mutation: {args}")

    def fake_run_gh_paginated_json(args, timeout=120):
        calls.append(args)
        if args[:2] == ["api", "repos/owner/repo/issues/7/comments"]:
            return []
        raise AssertionError(f"unexpected paginated gh api call: {args}")

    monkeypatch.setattr(core, "run_gh", fake_run_gh)
    monkeypatch.setattr(core, "run_gh_json", fake_run_gh_json)
    monkeypatch.setattr(core, "run_gh_paginated_json", fake_run_gh_paginated_json)

    result = core.post_or_update_summary_comment(ref, body, create=False)

    assert result == {"action": "skipped", "reason": "no_existing_comment"}
    assert not any("POST" in call or "PATCH" in call for call in calls)


def test_cmd_review_posts_comment_with_mocked_gh_and_llm(monkeypatch, tmp_path):
    ref = core.PullRequestRef("owner", "repo", 9)
    monkeypatch.setattr(core, "artifacts_root", lambda: tmp_path)
    monkeypatch.setattr(core, "fetch_pr_metadata", lambda _ref: {
        "number": 9,
        "title": "Fix bug",
        "baseRefName": "main",
        "headRefName": "feat/bug",
        "headRefOid": "abc123def456",
        "changedFiles": 1,
        "additions": 3,
        "deletions": 1,
        "url": "https://github.com/owner/repo/pull/9",
    })
    monkeypatch.setattr(core, "fetch_pr_diff", lambda _ref: "diff --git a/app.py b/app.py")
    monkeypatch.setattr(core, "fetch_pr_files", lambda _ref: [{"filename": "app.py"}])
    monkeypatch.setattr(core, "load_reviewer_config", lambda _ref, _base: {"config_path": None, "extra_doc_paths": [], "ignore_patterns": [], "config_error": None})
    monkeypatch.setattr(core, "collect_trusted_docs", lambda _ref, _base, extra_doc_paths=(): {"AGENTS.md": "Rules"})
    posted = {}
    monkeypatch.setattr(core, "post_or_update_summary_comment", lambda _ref, body: posted.setdefault("result", {"action": "created", "comment_id": "1", "url": "url"}) if core.SUMMARY_COMMENT_MARKER in body else None)

    class FakeLlm:
        def complete_structured(self, **kwargs):
            assert kwargs["purpose"] == "pr-review.review"
            return SimpleNamespace(parsed={"verdict": "comment", "risk": "low", "summary": "Looks fine", "findings": [], "verification_notes": ["mocked"]})

    args = argparse.Namespace(
        pr="owner/repo#9",
        no_llm=False,
        dry_run=False,
        max_diff_chars=5000,
        post_comment=True,
        allow_truncated_post=False,
        json=True,
    )

    rc = pr_review_cli._cmd_review(args, ctx=SimpleNamespace(llm=FakeLlm()))

    assert rc == 0
    assert posted["result"]["action"] == "created"
    review_path = tmp_path / ref.storage_name / "9" / "abc123def456" / "review.md"
    assert review_path.exists()


def test_cmd_review_post_findings_only_skips_zero_finding_comment(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "artifacts_root", lambda: tmp_path)
    monkeypatch.setattr(core, "fetch_pr_metadata", lambda _ref: {
        "number": 9,
        "title": "Clean change",
        "baseRefName": "main",
        "headRefName": "feat/clean",
        "headRefOid": "abc123def456",
        "changedFiles": 1,
        "additions": 3,
        "deletions": 1,
        "url": "https://github.com/owner/repo/pull/9",
    })
    monkeypatch.setattr(core, "fetch_pr_diff", lambda _ref: "diff --git a/app.py b/app.py")
    monkeypatch.setattr(core, "fetch_pr_files", lambda _ref: [{"filename": "app.py"}])
    monkeypatch.setattr(core, "load_reviewer_config", lambda _ref, _base: {"config_path": None, "extra_doc_paths": [], "ignore_patterns": [], "config_error": None})
    monkeypatch.setattr(core, "collect_trusted_docs", lambda _ref, _base, extra_doc_paths=(): {})
    posted = {"called": False}

    def fake_post(_ref, _body, *, create=True):
        posted["called"] = True
        posted["create"] = create
        return {"action": "skipped", "reason": "no_existing_comment"}

    monkeypatch.setattr(core, "post_or_update_summary_comment", fake_post)
    args = argparse.Namespace(
        pr="owner/repo#9",
        no_llm=True,
        dry_run=False,
        max_diff_chars=5000,
        post_comment=True,
        post_findings_only=True,
        allow_truncated_post=False,
        json=True,
    )

    payload = pr_review_cli._run_review(args, ctx=None)

    assert posted == {"called": True, "create": False}
    assert payload["comment"] == {"action": "skipped", "reason": "no_existing_comment", "findings": 0}
    assert payload["posting_policy"] == {
        "requested": True,
        "allow_truncated_post": False,
        "post_findings_only": True,
    }


def test_cmd_review_refuses_to_post_truncated_diff_without_override(monkeypatch, tmp_path, capsys):
    ref = core.PullRequestRef("owner", "repo", 9)
    monkeypatch.setattr(core, "artifacts_root", lambda: tmp_path)
    monkeypatch.setattr(core, "fetch_pr_metadata", lambda _ref: {
        "number": 9,
        "title": "Huge diff",
        "baseRefName": "main",
        "headRefName": "feat/huge",
        "headRefOid": "abc123def456",
        "changedFiles": 1,
        "additions": 1000,
        "deletions": 0,
        "url": "https://github.com/owner/repo/pull/9",
    })
    monkeypatch.setattr(core, "fetch_pr_diff", lambda _ref: "diff --git a/app.py b/app.py\n" + ("+x\n" * 1000))
    monkeypatch.setattr(core, "fetch_pr_files", lambda _ref: [{"filename": "app.py"}])
    monkeypatch.setattr(core, "load_reviewer_config", lambda _ref, _base: {"config_path": None, "extra_doc_paths": [], "ignore_patterns": [], "config_error": None})
    monkeypatch.setattr(core, "collect_trusted_docs", lambda _ref, _base, extra_doc_paths=(): {})
    posted = {"called": False}

    def fake_post(_ref, _body):
        posted["called"] = True
        return {"action": "created"}

    monkeypatch.setattr(core, "post_or_update_summary_comment", fake_post)

    args = argparse.Namespace(
        pr="owner/repo#9",
        no_llm=True,
        dry_run=False,
        max_diff_chars=1000,
        post_comment=True,
        allow_truncated_post=False,
        json=True,
    )

    rc = pr_review_cli._cmd_review(args, ctx=None)

    captured = capsys.readouterr()
    assert rc == 1
    assert not posted["called"]
    assert "Refusing to post" in captured.out
    review_path = tmp_path / ref.storage_name / "9" / "abc123def456" / "review.md"
    assert review_path.exists()


def test_cmd_review_allows_truncated_post_with_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "artifacts_root", lambda: tmp_path)
    monkeypatch.setattr(core, "fetch_pr_metadata", lambda _ref: {
        "number": 9,
        "title": "Huge diff",
        "baseRefName": "main",
        "headRefName": "feat/huge",
        "headRefOid": "abc123def456",
        "changedFiles": 1,
        "additions": 1000,
        "deletions": 0,
        "url": "https://github.com/owner/repo/pull/9",
    })
    monkeypatch.setattr(core, "fetch_pr_diff", lambda _ref: "diff --git a/app.py b/app.py\n" + ("+x\n" * 1000))
    monkeypatch.setattr(core, "fetch_pr_files", lambda _ref: [{"filename": "app.py"}])
    monkeypatch.setattr(core, "load_reviewer_config", lambda _ref, _base: {"config_path": None, "extra_doc_paths": [], "ignore_patterns": [], "config_error": None})
    monkeypatch.setattr(core, "collect_trusted_docs", lambda _ref, _base, extra_doc_paths=(): {})
    posted = {}
    monkeypatch.setattr(core, "post_or_update_summary_comment", lambda _ref, body: posted.setdefault("result", {"action": "created"}) if core.SUMMARY_COMMENT_MARKER in body else None)

    args = argparse.Namespace(
        pr="owner/repo#9",
        no_llm=True,
        dry_run=False,
        max_diff_chars=1000,
        post_comment=True,
        allow_truncated_post=True,
        json=True,
    )

    rc = pr_review_cli._cmd_review(args, ctx=None)

    assert rc == 0
    assert posted["result"]["action"] == "created"


def test_public_eval_manifest_parses_and_summarizes_seed_corpus():
    manifest = evals.load_eval_manifest()
    summary = evals.summarize_eval_manifest(manifest)

    assert manifest.schema_version == 1
    assert summary["case_count"] >= 8
    assert set(summary["categories"]).issubset(evals.CASE_CATEGORIES)
    assert "large-stress" in summary["categories"]
    assert summary["categories"]["small-docs"] == 1
    assert summary["observed_check_status"]["failure"] >= 1
    assert summary["totals"]["changed_files"] > 0
    assert summary["expectation_cases"] >= 3
    assert all("#" in ref and not ref.startswith(("private/", "NousResearch/")) for ref in summary["prs"])


def test_graph_promotion_manifest_focuses_default_readiness_cases():
    manifest = evals.load_eval_manifest(Path(evals.default_manifest_path()).with_name("graph_promotion_prs.json"))
    summary = evals.summarize_eval_manifest(manifest)

    assert summary["name"] == "graph-context-promotion"
    assert summary["case_count"] >= 9
    assert summary["categories"]["compiler-parser"] >= 3
    assert summary["categories"]["backend"] >= 2
    assert summary["categories"]["small-docs"] >= 1
    assert summary["categories"]["generated-dependency-heavy"] >= 1
    assert summary["expectation_cases"] == summary["case_count"]
    assert all("#" in ref and not ref.startswith(("private/", "NousResearch/")) for ref in summary["prs"])


def test_eval_manifest_rejects_duplicate_case_ids():
    raw = {
        "schema_version": 1,
        "name": "bad",
        "description": "bad",
        "cases": [
            {
                "id": "dup",
                "pr": "owner/repo#1",
                "category": "small-docs",
                "title": "One",
                "observed_head_sha": "abc",
                "observed_check_status": {"success": 1},
            },
            {
                "id": "dup",
                "pr": "owner/repo#2",
                "category": "backend",
                "title": "Two",
                "observed_head_sha": "def",
                "observed_check_status": {"success": 1},
            },
        ],
    }

    try:
        evals.parse_eval_manifest(raw)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected duplicate case id to fail")


def test_cmd_eval_manifest_prints_summary(capsys):
    args = argparse.Namespace(pr_review_command="eval-manifest", manifest=None, json=False)

    rc = pr_review_cli.pr_review_command(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert "public-oss-pr-review-beta" in captured.out
    assert "Cases:" in captured.out
    assert "large-stress" in captured.out
