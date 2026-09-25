# Hermes PR Review

Supervised, local-first pull request review through a Hermes skill and small Python helpers.

[![CI](https://github.com/itsreverence/hermes-plugin-pr-review/actions/workflows/ci.yml/badge.svg)](https://github.com/itsreverence/hermes-plugin-pr-review/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **Manual skill-first preview:** this branch makes the supervised skill the
> recommended path for manual reviews. It is not a stable release or a migration
> of the legacy plugin. See the [readiness record](docs/SKILL_FIRST_READINESS.md)
> for tested revisions and remaining publication gates.

## Review a PR with the skill

The [hermes-pr-review skill](skills/hermes-pr-review/SKILL.md) collects immutable
GitHub evidence, guides judgment in your current Hermes session, and saves a
validated local report. Python helpers handle claims, deduplication, evidence
limits, and commit checks. The model supplies the assessment.

1. Follow the [commit-pinned installation guide](docs/SKILL_FIRST_INSTALL.md).
2. Load `hermes-pr-review` in a supervised Hermes session and supply a PR URL.
   Ask explicitly for a full review when that is what you need, rather than triage.
3. Read the report's findings, coverage limits, and reviewed commits. A completed
   static assessment is not a merge approval.

The manual helpers require Python 3.11+ and authenticated `gh`. They do not require
plugin installation, systemd, Tailscale, CodeGraph, or a separate model SDK.
Linux is exercised. macOS has not been live-tested, and Windows is unsupported.
See [manual operation](docs/SKILL_FIRST.md) for helper commands.

## Supported boundary

- One supervised assessment of an explicitly selected PR.
- Pinned patches, bounded source, and guidance from the trusted base revision.
- Private local artifacts in a state root separate from legacy `pr-reviewer` data.
- Explicit incomplete outcomes for missing evidence, with bounded retries.
- No target code or tests executed and no GitHub comments, reviews, or merges.
- No automatic second pass, scheduled worker, or automatic repository enrollment.

The skill is not a sandbox: the surrounding Hermes session retains its configured
tools and credentials. Inspect findings before acting. A quiet report means no
supported introduced defect was found in that evidence, not that the PR is correct.

The optional native `judge` and shadow-scanner commands remain
[supervised experiments](docs/SKILL_FIRST.md#experimental-helpers-not-scheduled-operation).
Their presence in the bundle does not make unattended review supported.

## Legacy plugin

The existing plugin, webhook receiver, graph integration, and opt-in posting
remain available for existing installations. They are a separate path, with
separate requirements and [cutover gates](docs/SKILL_FIRST_ROLLOUT.md).
Installing the skill does not disable or replace them.

- [Legacy installation](docs/INSTALLATION.md)
- [Legacy operations and rollback](docs/OPERATIONS.md)
- [Legacy dogfood and webhook tests](docs/TESTING.md)

## Development and evidence

- [Current readiness and experiment decisions](docs/SKILL_FIRST_READINESS.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Development checks](docs/WORKFLOW.md)
- [Contributing](CONTRIBUTING.md)
- [Release policy](docs/RELEASING.md)
- [Changelog](CHANGELOG.md)
- [Support](SUPPORT.md) and [security reporting](SECURITY.md)

Raw review packets, provider output, webhook payloads, and installation receipts
may contain private repository information. Keep them outside this public
repository. Publish only inspected, sanitized evidence summaries. Never publish
provider credentials or webhook secrets.
