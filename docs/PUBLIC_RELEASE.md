# Public Beta Readiness

This repository is the public-beta distribution of Hermes PR Review. This document records what was proven before publication and what still gates the `v0.2.0` tag; remaining tag gates do not make the public beta unavailable.

## Current assessment

| Area | Status | Evidence / remaining work |
|---|---|---|
| Runtime | PASS | Local and public health, managed service lifecycle, scoped Funnel planning, owned webhook lifecycle, and no-post review flow are dogfooded. |
| Public install | PASS | Anonymous clone and the normal Hermes nested-plugin installer succeeded from a fresh profile with GitHub credentials disabled. CLI discovery and uninstall also passed. |
| CI | PASS | Public GitHub Actions passed the Python 3.11 and 3.12 plugin suites against a pinned Hermes Agent API checkout. |
| Diagnostics | PASS | `doctor`, service status/logs, Funnel status, and webhook status provide machine-readable output. Issue #1 tracks making required doctor failures return a nonzero process status. |
| Recovery | WARN | Core rollback paths are tested; managed secret rotation and fully guided stale-metadata repair remain post-beta improvements. |
| Public snapshot | PASS | The public repository began from one clean commit. Maintainer paths, private-project dogfood records, and private archive PR/comment evidence were removed; public OSS evals remain. |
| Security scan | PASS | Custom credential signatures and Gitleaks found no leaks in the release snapshot. GitHub vulnerability reporting and vulnerability alerts are enabled. |
| License | PASS | MIT license with the neutral project-contributor holder. |
| Repository controls | PASS | `main` requires Python 3.11/3.12 checks and resolved conversations; force pushes and branch deletion are blocked. |

## Repository topology

- Public distribution and active development: `itsreverence/hermes-plugin-pr-review`
- Private engineering archive: retained separately and not exposed through this repository's history

Future improvements should use the public repository's issue and pull-request workflow. The private archive exists only for historical engineering context.

## Completed public-distribution checks

- Current-tree scan for maintainer home paths, private project namespaces, private archive identifiers, and live tailnet hostnames.
- One-commit clean-history export and Gitleaks scan.
- Anonymous HTTPS clone with normal GitHub credentials disabled.
- Hermes nested-plugin install and enable from the public URL under a fresh `HERMES_HOME`.
- Plugin CLI discovery and `hermes pr-review --help`.
- Unauthenticated `doctor --json`, which correctly diagnosed missing `gh auth` and setup prerequisites.
- Plugin uninstall from the isolated profile.
- Public CI on Python 3.11 and 3.12.

A clean credential scan does not imply that arbitrary local review artifacts are safe to publish. Review artifacts and raw webhook payloads remain local/private by default.

## Pre-`v0.2.0` tag gates

1. Run a real low-risk webhook delivery canary against this public repository with posting disabled first.
2. Verify public update and rollback behavior between two published commits.
3. Fix or explicitly accept issue #1 (`doctor --json` exits zero when required checks fail).
4. Tag `v0.2.0` only after those lifecycle proofs pass.
