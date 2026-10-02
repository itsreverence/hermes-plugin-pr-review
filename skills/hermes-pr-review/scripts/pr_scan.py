#!/usr/bin/env python3
"""Explicit allowlisted shadow discovery only; no models, worker, or scheduler."""
import argparse
import json
from pathlib import Path
import sys

from pr_review_lib.scanner import Scanner, ScanGitHub, load_repos


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True, type=Path,
                        help="explicit private shared state root; uses separate scanner.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="enumerate every explicitly enrolled repository")
    scan.add_argument("--repos-file", required=True, type=Path, help="nonempty JSON list of exact owner/repo strings")
    scan.add_argument("--max-pages", type=int, default=20, help="per-repo cap including empty sentinel page")
    scan.add_argument("--max-requests", type=int, default=64, help="global cap including retries")
    scan.add_argument("--deadline", type=float, default=120, help="global GitHub time budget in seconds")
    commands.add_parser("status", help="inspect queue routing, not proof of review completion")
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            repos = load_repos(args.repos_file)  # Reject enrollment errors before state creation.
            github = ScanGitHub(max_pages=args.max_pages, max_requests=args.max_requests,
                                total_timeout=args.deadline)
            result = Scanner(args.state_root).scan(repos, github)
        else:
            result = Scanner(args.state_root).status()
        print(json.dumps({"status": "ok", **result}, sort_keys=True))
        return 0
    except Exception as exc:
        # Never wake from an incomplete scan, even with prior durable pending work.
        print(json.dumps({"status": "failed", "wakeAgent": False, "candidates": [],
                          "error": type(exc).__name__,
                          "detail": "Prior queue work is preserved; inspect arguments, private state, auth and budgets."}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
