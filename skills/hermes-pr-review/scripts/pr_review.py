#!/usr/bin/env python3
"""Manual-only entrypoint. No model, scheduler, plugin, or publishing client."""
import argparse
import json
import sys
from pathlib import Path

from pr_review_lib.state import State
from pr_review_lib.workflow import finalize, prepare


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
    finish = commands.add_parser("finalize", help="validate an agent result and recheck the PR snapshot")
    finish.add_argument("attempt")
    finish.add_argument("--result", required=True, type=Path)
    finish.add_argument("--model", required=True, help="operator-reported actual reviewing model, not inferred")
    status = commands.add_parser("status", help="inspect recent attempts or one exact attempt")
    status.add_argument("--attempt")
    fail = commands.add_parser("fail", help="record a review that could not finish")
    fail.add_argument("attempt")
    fail.add_argument("--reason", required=True, choices=["model_unavailable", "model_timeout", "operator_cancelled", "insufficient_context"])
    args = parser.parse_args(argv)
    try:
        state = State(args.state_root)
        if args.command == "status":
            result = state.get(args.attempt) if args.attempt else {"attempts": state.rows(), "limit": 100}
        elif args.command == "fail":
            result = state.finish(args.attempt, "failed", error=args.reason)
        else:
            from pr_review_lib.github import GitHub, parse_ref
            github = GitHub()
            if args.command == "prepare":
                result = prepare(state, github, parse_ref(args.pr), args.stage, rerun_reason=args.rerun_reason, allow_closed=args.allow_closed, allow_draft=args.allow_draft)
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
