# Architecture

Keep this file short. It exists so agents and humans can quickly understand the project boundary without rereading chat history.

## Purpose

Hermes PR Review is a local-first, public-beta candidate review workflow for GitHub pull requests. It aims to give Hermes users a CodeRabbit/Greptile-like review loop while staying Hermes-native:

- active Hermes model/provider/auth via plugin context;
- GitHub data through `gh`;
- trusted base-branch docs/config;
- structured diagnostics;
- local review artifacts;
- optional indexed graph context from an explicit local checkout;
- optional GitHub summary posting;
- future Kanban follow-through.

## Current layout

```text
plugins/pr_review/        plugin implementation
plugins/pr_review/evals/  public OSS eval manifest seed data
tests/plugins/            focused plugin tests
docs/                     versioned installation, operations, architecture, testing, and release guidance
```

The implementation is split by subsystem: `core.py` owns GitHub review context,
normalization, rendering, and posting; `graph_context.py` owns CodeGraph checkout
and index handling; `automation.py` owns watched-repo registry/state, watch runs,
webhook transport, status, and enablement; `onboarding.py` owns prerequisite
diagnostics, managed Linux user-systemd service lifecycle, Tailscale Funnel
inspection/setup, and explicit plan/apply GitHub webhook management; `dogfood.py` owns evaluation runs,
artifacts, observations, and scoring; `cli.py` owns command registration, review
orchestration, and thin dependency-injecting adapters for extracted subsystems.

## Manual skill-first candidate

`skills/hermes-pr-review/` contains the parallel, manual-only candidate. Its skill
owns review judgment and its standalone Python helpers own GitHub reads, SQLite
claims, deduplication, artifact writes, and result validation. It has no plugin
or receiver dependency and no scheduler or GitHub mutation implementation.
Review evidence includes full bounded files at head and merge base, plus selected
base guidance. Explicit extra source paths expand evidence without executing code.

Two opt-in experimental helpers share this bundle: a tool-less provider judgment
path and a deterministic allowlisted shadow scanner. Neither starts a scheduler.
The scanner owns a separate queue; its routing state is not a completed review.
An unattended worker must bind results back to the shared attempt ledger before
activation can be considered.
See [candidate design and scope](SKILL_FIRST.md). The legacy path below remains
intact until a separately approved cutover.

## Onboarding side-effect boundary

- `doctor`, service/Funnel status, webhook status, and webhook setup without `--apply` are read-only.
- Service installation writes only the marked user unit, creates/reuses the local mode-`0600` secret, and rolls the unit file back if reload/start fails.
- Remote GitHub webhook creation/update/removal requires `--apply`; the secret travels through `gh api --input -` and is never emitted in normal output.
- Service removal preserves secrets and review artifacts. The plugin does not expose Tailscale's device-wide Funnel reset.
- Automatic service lifecycle is Linux user-systemd only; other platforms run `webhook-serve` under their own process manager.

## Review flow

1. Parse PR reference.
2. Fetch PR metadata, changed files, checks, comments, and diff via `gh`.
3. Load trusted docs/config from the PR base branch.
4. Filter generated/vendor/ignored paths before model context.
5. Build capped context plus manifest and trace data.
6. Optionally collect indexed graph context from an explicit local checkout.
7. Call Hermes structured LLM review unless `--dry-run` / `--no-llm`.
8. Normalize findings into advisory diagnostics.
9. Write local artifacts.
10. Only post/update a persistent GitHub summary comment when `--post-comment` is passed.

## Non-goals for now

- No default-enabled Hermes core feature.
- No separate SaaS/dashboard.
- No automatic approve/request-changes GitHub reviews.
- No inline comments until summary comment dedupe and quality are proven.
- No untrusted PR code execution with secrets.
