# Public Beta Readiness

This repository is the public-beta distribution of Hermes PR Review. This document records what was proven before publication and what still gates the `v0.2.0` tag; remaining tag gates do not make the public beta unavailable.

## Current assessment

| Area | Status | Evidence / remaining work |
|---|---|---|
| Runtime | PASS | Local and public health, managed service lifecycle, scoped Funnel planning, owned webhook lifecycle, and no-post opened/synchronize/deduplication flow are dogfooded. |
| Public install | PASS | Anonymous clone and the normal Hermes nested-plugin installer succeeded from a fresh profile with GitHub credentials disabled. CLI discovery and uninstall also passed. |
| CI | PASS | Public GitHub Actions passed the Python 3.11 and 3.12 plugin suites against a pinned Hermes Agent API checkout. |
| Diagnostics | PASS | `doctor`, service status/logs, Funnel status, and webhook status provide machine-readable output. Issue #1 tracks a Hermes core dispatcher limitation that currently swallows the doctor's nonzero return status. |
| Recovery | PASS | Fresh-profile public install, force-reinstall update, pinned public-commit rollback, restore, and uninstall were exercised while preserving reviewer state outside the plugin directory. Managed secret rotation and fully guided stale-metadata repair remain post-beta improvements. |
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
- Owned public-repository webhook delivery for `opened` and `synchronize` with
  GitHub 202 responses, processed local spool records, exact-head artifacts,
  collected graph context, zero automated comments, and polling deduplication.
- Force-reinstall update from the public nested-plugin identifier, detached
  public-commit rollback, and restore to current public `main`, with reviewer
  state outside the plugin directory preserved.

A clean credential scan does not imply that arbitrary local review artifacts are safe to publish. Review artifacts and raw webhook payloads remain local/private by default.

## Pre-`v0.2.0` tag gates

1. Validate and either merge or explicitly accept the upstream Hermes core
   dependency tracked by issue #1 (`doctor --json` returns failure internally,
   but the current Hermes top-level dispatcher exits zero).
2. Run the exact release-tree test, lint, compile, link, credential-scan, and
   independent-review gates.
3. Tag `v0.2.0` only after those final gates pass.
