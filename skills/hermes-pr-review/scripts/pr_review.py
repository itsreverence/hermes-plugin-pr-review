#!/usr/bin/env python3
"""Local skill-first entrypoint. Optional tool-less judgment, no publishing."""
import argparse
import json
import sys
from pathlib import Path

from pr_review_lib.state import State
from pr_review_lib.workflow import finalize, prepare


def document_budget(value):
    """Operator-selected UTF-8 byte ceiling, never an unbounded PR override."""
    try:
        number = int(value)
        if 1 <= number <= 1_000_000:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("must be an integer from 1 to 1000000")


def dependency_budget(value):
    """Explicit dependency allowance; changed-source limits stay independent."""
    try:
        number = int(value)
        if 1 <= number <= 400_000:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("must be an integer from 1 to 400000")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path, help="explicit private state directory (separate from legacy pr-reviewer)")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("prepare", help="collect evidence, deduplicate, and claim manual work")
    collect.add_argument("pr")
    collect.add_argument("--stage", choices=["triage", "review"], default="triage")
    collect.add_argument("--rerun-reason", help="intentional rerun; cannot steal an active claim")
    collect.add_argument("--allow-closed", action="store_true", help="explicit retrospective review")
    collect.add_argument("--allow-draft", action="store_true")
    collect.add_argument("--source-path", action="append", default=[], help="explicit repository-relative source dependency, fetched at pinned head and merge base; repeatable")
    collect.add_argument("--max-doc-bytes", type=document_budget, default=60_000, help="total trusted-base document UTF-8 bytes (default: 60000; range: 1..1000000); omissions still block review")
    collect.add_argument("--max-dependency-bytes", type=dependency_budget, help="opt-in separate UTF-8 byte allowance for explicit unchanged dependencies at both pins (1..400000); changed-source limit remains 400000")
    finish = commands.add_parser("finalize", help="validate an agent result and recheck the PR snapshot")
    finish.add_argument("attempt")
    finish.add_argument("--result", required=True, type=Path)
    finish.add_argument("--model", required=True, help="operator-reported actual reviewing model, not inferred")
    judge = commands.add_parser("judge", help="experimental supervised tool-less judgment; no unattended worker")
    judge.add_argument("attempt")
    judge.add_argument("--provider", required=True, choices=["openai-codex"])
    judge.add_argument("--model", required=True)
    judge.add_argument("--reasoning", choices=["low", "medium", "high"], default="high")
    judge.add_argument("--timeout", type=int, default=180)
    status = commands.add_parser("status", help="inspect recent attempts or one exact attempt")
    status.add_argument("--attempt")
    fail = commands.add_parser("fail", help="record a review that could not finish")
    fail.add_argument("attempt")
    fail.add_argument("--reason", required=True, choices=["model_unavailable", "model_timeout", "operator_cancelled", "insufficient_context"])
    args = parser.parse_args(argv)
    if args.command == "prepare" and args.max_dependency_bytes is not None and (args.stage != "review" or not args.source_path):
        parser.error("--max-dependency-bytes requires --stage review and --source-path")
    try:
        state = State(args.state_root)
        if args.command == "status":
            result = state.get(args.attempt) if args.attempt else {"attempts": state.rows(), "limit": 100}
        elif args.command == "fail":
            result = state.finish(args.attempt, "failed", error=args.reason)
        else:
            from pr_review_lib.github import GitHub, parse_ref
            github = GitHub(max_doc_chars=args.max_doc_bytes, max_dependency_chars=args.max_dependency_bytes) if args.command == "prepare" else GitHub()
            if args.command == "prepare":
                result = prepare(state, github, parse_ref(args.pr), args.stage, rerun_reason=args.rerun_reason, allow_closed=args.allow_closed, allow_draft=args.allow_draft, source_paths=tuple(args.source_path))
            elif args.command == "judge":
                from pr_review_lib.judgment import judge_attempt
                result = judge_attempt(state, github, args.attempt, provider=args.provider, model=args.model, reasoning=args.reasoning, timeout=args.timeout)
            else:
                result = finalize(state, github, args.attempt, args.result, model=args.model)
        if "id" in result:
            result["artifact_dir"] = str(state.root / "attempts" / result["id"])
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") in (None, "prepared", "completed", "skipped") else 2
    except Exception as exc:
        # Error category only: provider stderr and private PR data never reach stdout.
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": "Inspect private state; verify arguments, permissions, auth, and attempt status."}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
