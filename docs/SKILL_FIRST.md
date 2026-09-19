# Manual skill-first candidate

This branch adds a manual-only replacement candidate alongside the existing
plugin. It does not install or enable the skill, modify the plugin, scan
repositories, create cron jobs, or change existing services/webhooks.

The candidate bundle is [`skills/hermes-pr-review`](../skills/hermes-pr-review/SKILL.md).
It runs without Hermes Python imports, plugin discovery, an HTTP receiver,
systemd, Tailscale, a graph index, or a separate model SDK. Python 3.11+ and `gh`
are the helper dependencies. Linux is exercised; macOS uses the same POSIX
primitives but has not been live-tested. Windows is not supported by this PoC.

## Manual operation

From a trusted copy of this repository, load the bundle's SKILL.md and references
into the default session. Do not install it globally during candidate testing.
Run the following commands through Hermes's `terminal` tool, replacing the PR
URL and private state path. These are ordinary Python commands, not plugin CLI.

```bash
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state prepare https://github.com/OWNER/REPO/pull/123 --stage triage
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state prepare https://github.com/OWNER/REPO/pull/123 --stage review
```

Resolve the helper path absolutely when operating outside this trusted checkout.
Never launch from a reviewed PR's working tree. Each admitted attempt returns an
ID and artifact directory. The agent reads context.md/input.json, writes a
judgment matching the result reference, then finalizes it:

```bash
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state finalize ATTEMPT_ID --result /private/judgment.json --model ACTUAL_MODEL
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state status --attempt ATTEMPT_ID
```

An explicitly requested rerun uses `--rerun-reason 'why'`. It retains the original
attempt. Closed/draft PRs need their explicit prepare flags. Model/provider failure
uses `fail ATTEMPT_ID --reason model_timeout` (or another enumerated reason).

`prepared` means collection only. `completed` means a validated static assessment
of included evidence, not approval. The helper records the actual reviewing model
as operator-reported provenance, not cryptographic attestation. Findings have
validated patch quotes and commit bindings; their reasoning still needs human
inspection. No GitHub mutation commands are implemented.

## Implementation

- `github.py`: strict PR references; GET-only, bounded GitHub reads; immutable
  compare/base-doc collection; conservative coverage and final collection check.
- `state.py`: private SQLite attempts, stage-separated dedupe, expiring claims,
  immutable attempt identity, failure retention, and short write transactions.
- `artifacts.py`: owner-private, no-overwrite, symlink-rejecting evidence files.
- `workflow.py`: shared prepare/finalize behavior, result/citation validation,
  final head/base recheck, local reports, and workflow/input digests.
- `pr_review.py`: thin explicit-state-root CLI, including status and failure recording.

The initial collector uses immutable GitHub compare output rather than mutable
PR-file pagination. Its server-side 300-file cap and count/patch checks fail
closed. It does not pretend that this is the future repository scanner's complete
pagination implementation. Scanning remains a separate next milestone.

## Verification

```bash
python -m pytest tests/skill_first -q
```

These tests use fixtures, real SQLite/filesystems, and separate processes for
claim races. They make no real model/GitHub requests. Run the legacy suite with
the Hermes source path as described in TESTING.md. CI covers both suites on
Python 3.11 and 3.12 and pins Ruff to the verified version instead of inheriting
new default rules from an unbounded upgrade.

Live evidence, when available, belongs in the candidate's private state root.
A public verification note records commands, input identities, outcomes, and
limits without committing raw PR packets, provider output, or private metadata.
Fixture success is not evidence of live model behavior or production deployment.
The [verification record](SKILL_FIRST_VERIFICATION.md) separates automated tests,
real manual runs, observed holds, and the remaining gates.

## What is not approved or implemented

No scheduled scanner, repository enrollment, installation, automatic review,
GitHub posting, service replacement, or live migration. The default session still
has its configured tools/credentials: this skill is not a sandbox. Unattended
reviews require a separate execution/credential restriction proof before rollout.

## Next gate and rollback

First prove real manual triage/review, unchanged-head reuse, intentional rerun,
and honest incomplete/failure handling. The initial live checks establish those
mechanics, but also expose missing surrounding-source context and overbroad
base-document collection. Address those bounded context gaps and evaluate real
positive findings before building a paginated allowlisted scanner in shadow mode.
Only after that consider one-candidate scheduled runs with a no-change wake gate.

Rollback of this candidate is to stop invoking its helper. Keep its private state
and artifacts for inspection. Existing plugin code, registry, watch-state,
services, hooks, and cron remain untouched; there is no live migration to undo.
