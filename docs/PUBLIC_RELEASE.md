# Public Release Readiness

This document separates runtime readiness from distribution readiness. Do not make the repository public until every release-decision item is resolved.

## Current assessment

| Area | Status | Evidence / remaining work |
|---|---|---|
| Runtime | PASS | Local and public health, managed service lifecycle, scoped Funnel planning, owned webhook lifecycle, and no-post review flow are dogfooded. |
| Install | PASS | The normal Hermes nested-plugin installer works from an isolated profile. |
| CI | PASS | Python 3.11 and 3.12 plugin suites run against a pinned Hermes Agent API checkout. |
| Diagnostics | PASS | `doctor`, service status/logs, Funnel status, and webhook status provide machine-readable output. |
| Recovery | WARN | Core rollback paths are tested; managed secret rotation and fully guided stale-metadata repair remain follow-ups. |
| Public snapshot | PASS | Current-tree maintainer paths and private-project dogfood records are removed; public OSS evals and repository-owned canaries remain. |
| Repository history | PASS | Public distribution will start from a clean snapshot; the existing private repository remains the engineering archive. |
| License | PASS | MIT license with the neutral project-contributor holder. |
| Visibility | PASS | Clean public distribution target: `itsreverence/hermes-plugin-pr-review`. |

## Completed hygiene checks

- Current-tree search for maintainer home paths, private project namespaces, and live tailnet hostnames.
- Full-history custom credential signature scan.
- Full-history Gitleaks scan with redaction enabled.
- Removal of private-project candidate matrices and dogfood reports from the release snapshot.
- Replacement of maintainer-specific development paths with generic examples.

A clean credential scan does not mean all historical metadata is appropriate to publish.

## Recommended release shape

Prefer a clean public distribution repository or clean initial snapshot if preserving private development history has little user value. Keep the existing private repository as the engineering archive until the public beta proves stable. This also avoids exposing historical pull requests, comments, and internal dogfood context merely by changing repository visibility.

If the existing repository will instead become public, audit GitHub pull requests, comments, Actions logs, releases, branches, tags, and other non-Git objects in addition to Git history.

## Remaining public-beta gates

1. Run the clean-room public install against the distribution repository.
2. Run a real low-risk webhook delivery canary before enabling posting for any repository.
3. Verify upgrade, rollback, and uninstall from an unauthenticated fresh profile.
4. Tag `v0.2.0` only after those lifecycle proofs pass.
