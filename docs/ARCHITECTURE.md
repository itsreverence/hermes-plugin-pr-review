# Architecture

## Supported manual path

Hermes PR Review separates model judgment from deterministic collection and
bookkeeping. A supervised Hermes session loads the shared skill and assesses an
explicit PR. Small Python helpers collect evidence and validate the local result.
No plugin, receiver, graph index, or scheduler is needed for that path.

```text
skills/hermes-pr-review/                  shared instructions and references
skills/hermes-pr-review/scripts/          CLI helpers
skills/hermes-pr-review/scripts/pr_review_lib/
  github.py                              bounded GET-only GitHub collection
  state.py                               private SQLite claims and deduplication
  artifacts.py                           immutable private evidence files
  workflow.py                            admission, finalization, and reports
  judgment.py                            optional inference and finalization orchestration
  native_transport.py                    experimental Hermes provider adapter
  scanner.py                             experimental allowlisted discovery queue
tests/skill_first/                        helper and integration tests
plugins/pr_review/                       preserved legacy implementation
tests/plugins/                          legacy implementation tests
docs/                                   operation, verification, and history
```

### Data flow

1. `prepare` validates the PR reference and captures immutable head, base, and
   merge-base identities. It collects metadata, patches, bounded source, and
   relevant guidance from the trusted base. Explicit dependencies are opt-in.
2. Admission checks evidence limits and claims the snapshot in private SQLite
   state. Missing evidence remains incomplete. A prepared packet is collection,
   not a completed review.
3. The supervised session reads the packet as data and writes a separate judgment.
4. `finalize` validates the judgment, patch quotations, and input/workflow bindings.
   It rechecks the remote head and base before recording the outcome.
5. The operator reads the exact ledger row and rendered report. A completed static
   assessment is not a merge approval or a successful runtime test.

PR text and source are untrusted. Neither helper collection nor model assessment
executes target code. The manual path implements no GitHub writes. The surrounding
Hermes session still has tools and credentials, so supervision is a real boundary,
not a sandbox claim. See [manual operation](SKILL_FIRST.md).

### Experimental helpers

The optional `judge` command supplies tool-less inference through Hermes's provider
stack. `pr_scan.py` performs allowlisted shadow discovery into a separate queue.
Neither installs a schedule or proves unattended isolation. Queue state is not a
completed review. Exclusive inference ownership, lease budgeting, credential
restrictions, and verified queue-to-attempt bindings remain separate
[rollout gates](SKILL_FIRST_ROLLOUT.md).

## Preserved legacy plugin

The legacy path obtains the active model through plugin context. `core.py` handles
review context, normalization, rendering, and posting. `graph_context.py` manages
optional CodeGraph checkout/index context. `automation.py` owns watched-repository
state and webhook transport. `onboarding.py` manages diagnostics, user-systemd
services, Funnel inspection, and explicit webhook plan/apply operations.
`dogfood.py` owns evaluation artifacts and scoring. `cli.py` registers commands.

Legacy review can use indexed graph context and opt-in summary comments. Those
features are not inherited by the skill. Skill installation leaves plugin code,
registry, review state, receiver, hooks, and schedules intact.

Legacy onboarding also has separate side-effect boundaries: service installation
writes a marked user unit and a private secret, while webhook writes require
`--apply`. Service removal preserves secrets and review artifacts. The plugin does
not reset device-wide Tailscale routing. See [legacy installation](INSTALLATION.md)
and [operations and rollback](OPERATIONS.md).

## Evidence and release boundaries

[Readiness](SKILL_FIRST_READINESS.md) distinguishes exact-revision implementation
checks, real static reviews, historical experiments, and deferred activation.
Neither a quiet review nor a successful install establishes detection accuracy,
unattended safety, or legacy cutover readiness.
