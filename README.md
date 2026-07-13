# Hermes PR Review

Hermes-first pull request reviews with structured diagnostics, local artifacts, and opt-in GitHub automation.

[![CI](https://github.com/itsreverence/hermes-plugin-pr-review/actions/workflows/ci.yml/badge.svg)](https://github.com/itsreverence/hermes-plugin-pr-review/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> **Public beta:** install from `main`. There is not yet a tagged stable release. GitHub comment posting is disabled by default.

Hermes PR Review uses your configured Hermes model and authentication, gathers pull-request data through `gh`, loads reviewer instructions from the trusted base branch, and writes inspectable local artifacts. It does **not** execute pull-request code.

## Requirements

- Hermes Agent with third-party plugin installation
- Git and authenticated GitHub CLI (`gh`)
- Linux with user systemd for managed webhook-service installation
- Tailscale Funnel or another HTTPS reverse proxy for event-driven automation
- Optional CodeGraph CLI and local index for graph-backed context

Direct reviews work without systemd, a public webhook, or CodeGraph.

## Install

```bash
hermes plugins install itsreverence/hermes-plugin-pr-review/plugins/pr_review --enable
hermes pr-review doctor
```

Nested plugin installs update by reinstalling the same identifier:

```bash
hermes plugins install itsreverence/hermes-plugin-pr-review/plugins/pr_review --force --enable
```

## First no-post review

```bash
hermes pr-review review OWNER/REPO#123 --json
```

Review artifacts are written as owner-only files (`0600`) under owner-only managed directories (`0700`):

```text
~/.hermes/pr-reviewer/reviews/OWNER_REPO/PR/HEADSHA/
```

Typical artifacts include the collected context, manifest, structured findings, rendered review, and trace. No GitHub comment is created unless `--post-comment` is explicitly supplied.

To enable a repository for watched or webhook reviews while keeping posting off:

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout
```

The local checkout supplies trusted base-branch context. Optional graph setup:

```bash
hermes pr-review graph-setup --local-repo /path/to/checkout --install-missing
hermes pr-review enable OWNER/REPO \
  --local-repo /path/to/checkout \
  --graph-context auto \
  --graph-context-binary codegraph
```

## Automated webhook path

The supported Linux path is:

1. enable a repository;
2. install the user-systemd receiver;
3. expose it through Tailscale Funnel or another HTTPS proxy;
4. plan and explicitly apply the GitHub webhook;
5. verify a real opened/synchronized pull-request delivery in no-post mode.

See [Installation](docs/INSTALLATION.md) for the complete setup and [Operations](docs/OPERATIONS.md) for status, recovery, rollback, and removal.

## Safety defaults

- GitHub posting defaults to off per repository.
- Remote onboarding and destructive operations require `--apply`.
- Truncated diffs are not posted by watched/webhook reviews.
- Webhook requests require GitHub SHA-256 HMAC verification.
- The managed receiver binds to loopback and runs as a user service, never root.
- Pull-request code is treated as untrusted and is not executed.
- Reviewer config and instructions come from the trusted base branch.

Before enabling posting, inspect several no-post reviews and prove a repository-specific webhook canary. See [Testing](docs/TESTING.md).

## Privacy

Review artifacts, dogfood summaries and copied artifacts, webhook payloads, service journals, and status output may contain repository names, PR text, paths, URLs, diagnostics, and review content. New artifact outputs use owner-only files and directories. Never publish the webhook secret, provider credentials, raw private-repository payloads, or an unreviewed artifact directory.

## Documentation and support

- [Installation](docs/INSTALLATION.md)
- [Operations and rollback](docs/OPERATIONS.md)
- [Architecture and trust boundaries](docs/ARCHITECTURE.md)
- [Testing, dogfood, and posting canaries](docs/TESTING.md)
- [Release status and process](docs/RELEASING.md)
- [Support](SUPPORT.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Uninstall

```bash
hermes pr-review webhook remove OWNER/REPO --hook-id HOOK_ID --apply
hermes pr-review disable OWNER/REPO --apply
hermes pr-review service remove --apply
hermes plugins remove pr-review
```

Service removal preserves the local webhook secret and review artifacts. The plugin intentionally does not perform a device-wide Tailscale Funnel reset.
