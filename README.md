# Hermes PR Review

Public-beta candidate, local-first PR review workflow for Hermes Agent.

The reviewer uses Hermes' configured model/auth, GitHub CLI, trusted base-branch repository docs, structured diagnostics, and local artifacts. It does **not** execute pull-request code. GitHub posting is disabled for newly enabled repositories until the operator explicitly turns it on.

> **Availability:** this MIT-licensed repository is the public-beta distribution. The onboarding flow is implemented and dogfooded; keep GitHub posting disabled until a repository-specific canary earns it.

## Supported public-beta path

- Hermes Agent with third-party plugin installation
- Linux with user systemd for the managed webhook receiver
- Git and authenticated GitHub CLI (`gh`)
- Tailscale Funnel, or another public HTTPS reverse proxy
- CodeGraph is optional; baseline reviews work without Node/npm or a graph index

macOS and other process managers can run `webhook-serve` directly, but automatic service installation currently supports Linux user systemd only.

## Install

Install the plugin directly from the repository subdirectory:

```bash
hermes plugins install itsreverence/hermes-plugin-pr-review/plugins/pr_review --enable
hermes pr-review doctor
```

Update or remove it with the standard Hermes plugin commands:

```bash
hermes plugins update pr-review
hermes plugins remove pr-review
```

For local development, clone this repository and run `./scripts/install-dev.sh`; that symlinks the checkout into the active Hermes profile. Do not use the development installer for a normal user installation.

## Zero-to-first-review quickstart

The commands below keep posting disabled and preserve the webhook secret locally.

### 1. Enable a repository

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout
```

### 2. Optionally prepare CodeGraph

Skip this step for baseline reviews. To enable graph-backed auto mode:

```bash
hermes pr-review graph-setup --local-repo /path/to/checkout --install-missing
hermes pr-review enable OWNER/REPO \
  --local-repo /path/to/checkout \
  --graph-context auto \
  --graph-context-binary codegraph
```

`enable` resolves and persists the stable CodeGraph launcher so webhook reviews do not depend on a login-shell `PATH`.
Remove an obsolete persisted launcher with `hermes pr-review enable OWNER/REPO --clear-graph-context-binary`.

### 3. Install the durable receiver

```bash
hermes pr-review service install
hermes pr-review service status
hermes pr-review service restart
```

This creates a managed **user** systemd unit, creates or reuses a mode-`0600` webhook secret, enables the unit, and starts or restarts the receiver. It never installs a root service.

### 4. Expose the receiver through Tailscale Funnel

```bash
hermes pr-review funnel setup
hermes pr-review funnel setup --apply
```

The first command verifies the local receiver and shows the plan without changing Tailscale. It refuses to replace an unrelated existing Funnel route. `--apply` configures Funnel in noninteractive background mode, discovers the public hostname, and verifies the public `/healthz` endpoint. Copy the returned `webhook_url`.

If Tailscale is not appropriate, expose `http://127.0.0.1:8787` through another HTTPS proxy and use `https://YOUR_HOST/webhooks/github` below.

### 5. Plan and create the GitHub webhook

Plan mode performs no remote mutation:

```bash
hermes pr-review webhook setup OWNER/REPO \
  --url https://YOUR_HOST/webhooks/github
```

Review the plan, then explicitly apply it:

```bash
hermes pr-review webhook setup OWNER/REPO \
  --url https://YOUR_HOST/webhooks/github \
  --apply
```

The webhook is active, uses `application/json`, verifies TLS, subscribes only to pull-request events, and receives the secret through `gh api` stdin rather than command-line arguments or normal output.

### 6. Verify the whole installation

```bash
hermes pr-review doctor
hermes pr-review service status
hermes pr-review funnel status --verify
hermes pr-review webhook status OWNER/REPO
hermes pr-review status --github-repo OWNER/REPO --github-hook-id HOOK_ID
```

Open or synchronize a pull request, then rerun `status`. A real graph-backed review is green only when the review succeeded, was not a `--no-llm` smoke, and recorded collected graph context.

## Safe rollback

Remote and destructive operations require `--apply`:

```bash
hermes pr-review webhook remove OWNER/REPO --hook-id HOOK_ID --apply
hermes pr-review disable OWNER/REPO --apply
hermes pr-review service remove --apply
hermes plugins remove pr-review
```

The service removal preserves the webhook secret and local review artifacts. There is intentionally no automated Funnel reset: Tailscale's reset operation is device-wide and could remove unrelated routes. Inspect `tailscale funnel status` and remove only the reviewer mapping using the scoped operation supported by the Tailscale version installed on that device.

## Useful commands

```bash
hermes pr-review doctor --json
hermes pr-review review OWNER/REPO#123 --json
hermes pr-review watch-run --no-llm --json
hermes pr-review service logs --lines 100
hermes pr-review graph-health --local-repo /path/to/checkout --json
```

`--post-comment` is intentionally omitted. Use it only after reviewing local no-post artifacts. If a review diff was truncated, posting is refused unless `--allow-truncated-post` is also passed after manual review.

Artifacts are written under:

```text
~/.hermes/pr-reviewer/reviews/OWNER_REPO/PR/HEADSHA/
```

Typical outputs include `context.md`, `context-manifest.json`, `findings.json`, `review.md`, `review-trace.json`, and graph-context files when CodeGraph is used.

## Privacy and troubleshooting output

Receiver journals, status JSON, delivery spool entries, and review artifacts may contain repository names, PR URLs, paths, diagnostics, and review text. They do not intentionally print the webhook secret, but inspect and redact all output before sharing it publicly.

Start with bounded diagnostics and share only the smallest relevant excerpt:

```bash
hermes pr-review doctor --json
hermes pr-review service status --json
hermes pr-review service logs --lines 100
hermes pr-review funnel status --verify --json
```

Never publish `~/.hermes/pr-reviewer/webhook-secret`, raw webhook payloads, provider credentials, or an unreviewed copy of `~/.hermes/pr-reviewer/`.

## Optional indexed graph context

`--graph-context` is an experimental, opt-in path for adding compact structural codebase context from an explicit local checkout (`--local-repo`). `--graph-context-auto` is the safer repo-default shape: it uses CodeGraph only when that checkout already has an initialized `.codegraph/` index and falls back to baseline if the index is missing/unhealthy. The plugin does not run provider installers, edit MCP/agent config, execute PR code, or post comments because graph context was collected.

Provider:

- Graph context uses the CodeGraph CLI (`CODEGRAPH_BINARY` or `codegraph` on `PATH`) and allows its local `.codegraph/` index directory as the only expected checkout mutation.

Use `--graph-context-binary /path/to/binary` to point at the CodeGraph executable. For watch/webhook services, persist a stable launcher with `hermes pr-review enable OWNER/REPO --graph-context-binary /absolute/path/to/codegraph`; the launcher directory is prepended to the subprocess `PATH` so interpreter-backed npm/mise launchers can find their sibling runtime without a login shell. The default index mode hint is `fast`. Injected graph markdown is capped by `--max-graph-context-chars` (default: 12000); full graph markdown/raw summaries are still written as local artifacts with absolute checkout/binary paths omitted.

Check whether a checkout is ready for auto mode before enabling it:

```bash
hermes pr-review graph-health --local-repo /path/to/checkout --json
hermes pr-review graph-health --local-repo /path/to/checkout --sync
```

For one-command local setup, use `graph-setup`. It can install the CodeGraph CLI with npm when explicitly requested, initializes/syncs `.codegraph/`, and ignores the local index via `.git/info/exclude` by default so setup does not dirty the project:

```bash
hermes pr-review graph-setup --local-repo /path/to/checkout --install-missing
```

Use `--ignore-mode gitignore` when the project wants to commit `.codegraph/` to `.gitignore`; use `--ignore-mode none` if the repo already ignores the index another way.

The health check reports checkout cleanliness, HEAD, `.codegraph/` presence/size, CodeGraph status counts, optional sync result, and whether the checkout is ready for `--graph-context-auto`.

## Auto-review watched repos

`watch-run` is the local-first equivalent of “enable this reviewer on selected repos.” It polls configured repositories for open PRs, reviews PR heads it has not seen before, and records reviewed heads so repeat runs do not spam the same commit.

For event-driven pilots, `webhook-event` processes one GitHub `pull_request` webhook payload through the same local registry/state/review path. `webhook-serve` is the small local HTTP receiver for GitHub webhooks; it is designed to bind to localhost and sit behind Tailscale Funnel or another HTTPS proxy. Both stay inside the plugin instead of requiring Hermes core changes.

Create the local registry with `enable`; posting remains disabled unless explicitly changed:

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout
```

The managed receiver, Funnel, and GitHub commands from the quickstart all use this same registry and secret. For temporary debugging, the lower-level commands remain available:

```bash
hermes pr-review watch-run --no-llm --json
hermes pr-review webhook-event --payload /tmp/github-pull-request.json --event pull_request --delivery DELIVERY_ID --json
hermes pr-review webhook-serve --host 127.0.0.1 --port 8787 --secret-file ~/.hermes/pr-reviewer/webhook-secret
```

The registry may also record the public webhook URL and GitHub hook ID after `webhook setup --apply`, allowing later diagnostics without storing the webhook secret in JSON.

`webhook-serve` verifies GitHub's SHA-256 signature, accepts only the GitHub webhook path and health endpoint, caps request sizes and read times, and durably spools accepted deliveries before acknowledgement. A bounded worker serializes review/watch-state updates across receiver processes; startup recovery safely replays stranded accepted deliveries after the health listener binds. Unsupported events/actions are ignored before entering the review queue, and supported requests receive a busy response instead of creating unbounded concurrent reviews. Detailed durability, locking, and replay semantics live in `docs/ARCHITECTURE.md` and `docs/WORKFLOW.md`.


`webhook-event` accepts GitHub `pull_request` payloads for `opened`, `synchronize`, `reopened`, and `ready_for_review`. It ignores unsupported actions, disabled repos, draft PRs unless `reviewDrafts: true`, PRs that are no longer open, stale payload heads that no longer match GitHub's current PR head, and already-reviewed heads. It records the delivery/action metadata into watch state when it reviews or fails a head, so later status/history work can explain what triggered the run.

Use `hermes pr-review status` (alias: `webhook-status`) to inspect the local setup without digging through JSON files. It reports registry entries, per-repo graph readiness and setup next steps, secret-file safety, receiver `/healthz`, watch-state review counts, recent delivery spool status, and actionable next steps for WARN/FAIL states. Receiver downtime and missing first-run delivery/state files are warnings; invalid secret paths are failures.

Registry notes:

- `postComment` defaults to `false`; turn it on per repo only after no-post dogfood looks good.
- `postFindingsOnly` defaults to `true`; when posting is enabled through watch/webhook canaries, clean zero-finding reviews still record local artifacts and update an existing managed comment if present, but skip creating new “looks good” comments. Set `postFindingsOnly: false` only after a repo graduates to always-post managed summary comments.
- Truncated-diff posting is blocked by default for watch/webhook runs; only direct `review --post-comment --allow-truncated-post` can override it after manual artifact review.
- `graphContext: "auto"` uses CodeGraph only when the configured local checkout has a healthy existing `.codegraph/` index.
- State is saved to `~/.hermes/pr-reviewer/watch-state.json`; the same PR head is skipped until it changes or `--force` is passed.

## Local development

This repo currently expects a local Hermes Agent checkout for plugin APIs during tests.

```bash
export HERMES_AGENT_SRC=/path/to/hermes-agent
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m pytest tests/plugins -q
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m py_compile plugins/pr_review/*.py
```

## Development setup example

Expected development setup:

```text
~/.hermes/plugins/pr-review -> /path/to/hermes-pr-review/plugins/pr_review
hermes plugins list: pr-review enabled, source=user
hermes pr-review setup: gh auth ok
hermes pr-review eval-manifest --json: success true, bundled corpus validates
hermes pr-review review NousResearch/hermes-agent#50842 --json: success true, no post
```

## Product boundary

Target for now:

```text
external/local Hermes plugin → dogfood → harden → maybe upstream later as opt-in plugin/docs/API proposal
```

Not the target yet:

```text
default-enabled Hermes core feature
```

See:

- `docs/ARCHITECTURE.md` — concise system map
- `docs/WORKFLOW.md` — install/test/dogfood workflow
- `docs/PUBLIC_RELEASE.md` — distribution-readiness gates and release decisions
- `plugins/pr_review/evals/public_prs.json` — bundled public OSS eval corpus
