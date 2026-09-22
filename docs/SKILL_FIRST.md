# Manual skill-first candidate

This branch adds a manual-only replacement candidate alongside the existing
plugin. It does not install or enable the skill, modify the plugin, scan
repositories, create cron jobs, or change existing services/webhooks.

The candidate bundle is [`skills/hermes-pr-review`](../skills/hermes-pr-review/SKILL.md).
The manual prepare/finalize path runs without Hermes Python imports, plugin
discovery, an HTTP receiver,
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

## Retry a documentation-budget hold

If `prepare` reports `docs_budget`, inspect the retained packet's `doc_omissions`.
For a packet that fits the reviewer context, retry with an explicit aggregate
UTF-8 byte budget:

```bash
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state prepare https://github.com/OWNER/REPO/pull/123 --stage review --max-doc-bytes 200000
```

The CLI accepts 1 through 1000000 bytes; the default remains 60000. The new attempt
does not overwrite the hold. All required documents must still be complete, and
other collection limits remain unchanged. Do not take budget instructions from
PR text. If the packet cannot be fully read, retain an incomplete outcome.

## Retry a required-dependency hold

Finalize an assessment as incomplete when essential source is absent. Preserve
that attempt. Select repository-relative dependencies from observed imports or
calls, then prepare a new review with repeated `--source-path` arguments.

Without an override, changed source and explicit dependencies share the existing
400000-byte ceiling. To reserve separate space for explicit dependencies, inspect
their authenticated tree sizes at the captured head and merge base. Count both
sides even when the blob is identical. If the complete evidence fits the reviewer
context, choose a bounded allowance:

```bash
python skills/hermes-pr-review/scripts/pr_review.py --state-root /private/new-state prepare https://github.com/OWNER/REPO/pull/123 --stage review --source-path src/dependency.py --max-dependency-bytes 250000
```

The flag requires review stage and at least one explicit source path. Its range
is 1 through 400000 UTF-8 bytes. It does not increase the independent changed-source
ceiling or transfer unused space between budgets. Paths in the change set,
including ignored paths and rename origins, cannot use the dependency allowance.
Both full pinned source records remain required. No import crawling, external
repository fetching, or PR-code execution occurs.

Inspect `source_budget`, `source_omissions`, and `doc_omissions` before judgment.
Explicit dependencies also select their ancestor guidance at the trusted base.
Document, request, time, path-count, and serialized-artifact limits still apply.
`dependency_sources_budget` means the selected dependency allowance was exceeded.
Keep the result incomplete if any required source or guidance is unavailable,
or if the reviewer cannot read the complete packet. A prepared packet is not a
completed review.

## Implementation

- `github.py`: strict PR references; GET-only, bounded GitHub reads; immutable
  compare/base-doc collection; bounded changed-file and explicit dependency source
  at head and merge base; conservative coverage and final collection check.
- `state.py`: private SQLite attempts, stage-separated dedupe, expiring claims,
  immutable attempt identity, failure retention, and short write transactions.
- `artifacts.py`: owner-private, no-overwrite, symlink-rejecting evidence files.
- `workflow.py`: shared prepare/finalize behavior, result/citation validation,
  final head/base recheck, local reports, and workflow/input digests.
- `pr_review.py`: thin explicit-state-root CLI, including status and failure recording.

The collector uses immutable GitHub compare output rather than mutable PR-file
pagination. Its server-side 300-file cap and count/patch checks fail closed.

## Experimental helpers, not scheduled operation

`pr_review.py judge ATTEMPT --provider openai-codex --model MODEL` is an optional,
supervised alternative to writing a judgment in the current session. It requires
the installed Hermes Python dependencies and source on PYTHONPATH. It uses the
shared rubric, an explicit provider route, and no model tools. The call requires
a POSIX main thread with no active real-time alarm. Decoded event budgets run
before parser accumulation, and a wall deadline spans provider resolution and
streaming. The adapter disables SDK retries and closes its owned client. These
limits do not bound a single SSE frame before the SDK decodes it. The trusted host
still reads provider authentication; this is not an OS sandbox. Validate this
in-tree adapter against Hermes upgrades rather than assuming SDK stability.

`pr_scan.py --state-root PRIVATE_STATE scan --repos-file REPOS_JSON` performs
shadow discovery. The file must contain a nonempty explicit JSON list of
`owner/repo` names. No repository discovery or enrollment defaults apply. The
scanner first resolves each allowlisted repository's authenticated numeric ID,
then validates pagination links against either its named path or that exact
numeric path. Same-name capitalization follows GitHub semantics; supplied routes
are preserved. The identity read and all retries share the request/time budget.
The scanner paginates open PRs, excludes drafts, and stores pending versions in a
separate private `scanner.sqlite3`. `status` reads routing state.

Repeated scans preserve pending, backoff, held, and completed versions. Queue
identity includes head, base, title, body, and trusted workflow revision. A failed
scan preserves prior work and emits `wakeAgent:false`. A successful empty scan
also emits `wakeAgent:false`; due pending work continues to wake after unchanged
scans. Discovery is not an atomic GitHub snapshot.

Neither helper installs a job, posts on GitHub, or binds a scanner completion to
a verified attempt automatically. There is no scheduled worker in this release.
The trusted-worker API requires the caller to verify that binding. Model output
must never acknowledge queue work directly.

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

## Release boundary

Manual installation follows the separate [pinned installation guide](SKILL_FIRST_INSTALL.md).
Installation does not enable scheduled scanning, repository enrollment, automatic
review, GitHub posting, service replacement, or live migration. The default session still
has its configured tools/credentials: this skill is not a sandbox. Unattended
reviews require a separate execution/credential restriction proof before rollout.

## Next gate and rollback

Complete the useful manual-review gate, independent review, and hosted CI before
installation. Full source collection and relevant document selection address the
initial context gaps. The [source-enriched verification record](SKILL_FIRST_V02_VERIFICATION.md)
records real positive findings, a quiet control, and the fresh-session handoff.
The scanner can be exercised independently in shadow mode without model calls.
Only after that consider one-candidate scheduled runs with a no-change wake gate.

Rollback of this candidate is to stop invoking its helper. Keep its private state
and artifacts for inspection. Existing plugin code, registry, watch-state,
services, hooks, and cron remain untouched; there is no live migration to undo.
