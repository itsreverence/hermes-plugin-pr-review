# Workflow

Keep this file practical. It should answer: how do we safely change, test, dogfood, and eventually publish this thing?

## Public install and durable receiver

Normal users install from the plugin subdirectory rather than cloning and symlinking a checkout:

```bash
hermes plugins install itsreverence/hermes-plugin-pr-review/plugins/pr_review --enable
hermes pr-review doctor
```

The repository must be public (or the user must already have Git credentials for it) before that command is broadly usable.

Linux user-systemd path:

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout
hermes pr-review service install
hermes pr-review funnel setup
hermes pr-review funnel setup --apply
hermes pr-review webhook setup OWNER/REPO --url https://HOST/webhooks/github
hermes pr-review webhook setup OWNER/REPO --url https://HOST/webhooks/github --apply
```

The first webhook command is plan-only. `--apply` is required for remote creation/update and removal. Service removal also requires `--apply`, preserves secrets/artifacts, and refuses to touch an unmanaged unit unless the operator explicitly passes `--force`.

Operational checks:

```bash
hermes pr-review doctor
hermes pr-review service status
hermes pr-review service restart
hermes pr-review service logs --lines 100
hermes pr-review funnel status --verify
hermes pr-review webhook status OWNER/REPO
```

The plugin intentionally does not expose Tailscale's device-wide Funnel reset. Inspect `tailscale funnel status` and remove only the reviewer mapping with the scoped operation supported by the installed Tailscale version.

## Install/update local development plugin

```bash
./scripts/install-dev.sh
```

Expected development state:

```text
~/.hermes/plugins/pr-review -> /path/to/hermes-pr-review/plugins/pr_review
hermes plugins list: pr-review enabled, source=user
hermes pr-review doctor: required checks ready
```

## Local test gate

```bash
export HERMES_AGENT_SRC=/path/to/hermes-agent
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m pytest tests/plugins -q
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m py_compile plugins/pr_review/*.py
```

## Dogfood gate

Run no-post reviews first. Start with the bundled public OSS manifest in `plugins/pr_review/evals/public_prs.json`, then add repository-owned canaries only when their evidence is safe to publish.

For a repeatable harness run, use `dogfood-run`. It never sets `--post-comment`; it writes a machine-readable JSON summary, a markdown inspection worksheet, and rolling observation history files under `evals/dogfood-runs/` (`observations.jsonl` plus `observations-summary.json`).

```bash
hermes pr-review dogfood-run --no-llm --limit 1 --json
hermes pr-review dogfood-run --case small-docs-fastapi-15815 --json
hermes pr-review dogfood-run \
  --case browser-tooling-playwright-41396 \
  --variant baseline --variant graph \
  --graph-local-repo-map /path/to/graph-repos.json \
  --graph-context-binary codegraph \
  --json
```

Use `--no-llm` for fast context/artifact smokes. Omit it only when running inside Hermes with model access and enough budget for real reviews. The paired `baseline`/`graph` dogfood variant flow runs each selected case twice, keeps posting disabled, and adds a `variant_comparisons` section to the JSON/markdown output. The graph local-repo map is a JSON object keyed by case id or `owner/repo#number` PR ref.

The bundled `public_prs.json` manifest is the broad seed corpus. Use `plugins/pr_review/evals/graph_promotion_prs.json` when deciding whether graph context should become default-on; it intentionally overweights known-finding/compiler/backend cases and keeps a couple clean controls for noise detection:

```bash
hermes pr-review eval-manifest plugins/pr_review/evals/graph_promotion_prs.json
hermes pr-review dogfood-run plugins/pr_review/evals/graph_promotion_prs.json \
  --variant baseline --variant graph \
  --graph-local-repo-map /path/to/graph-repos.json \
  --graph-context-binary codegraph \
  --run-id graph-promotion-001 \
  --json
```

Manifest cases may include optional `expectations` to turn a dogfood run into a regression gate. Start with conservative fields: `expected_findings_max`, `expected_risk`, `expected_truncated`, `expected_docs_loaded_min`, and `expected_posted_comments`.

Every dogfood summary also includes a manual scoring guide and per-case worksheet fields. Use the posting-quality buckets for both single-variant and paired runs:

- `post_worthy`
- `useful_but_edit`
- `artifact_only`
- `noise`
- `miss`

Score paired baseline/graph runs with optional graph comparison buckets:

- `same_useful_finding`
- `baseline_sharper` / `graph_sharper`
- `graph_missed_useful_baseline` / `baseline_missed_useful_graph`
- `graph_reduced_noise` / `graph_introduced_noise`
- `both_noise`
- `expectation_should_change` / `expectation_should_hold`

For each finding, check that it is actionable, evidence-cited, severity-appropriate, non-duplicative, and safe to post publicly. Treat `expected_findings_max` failures as prompts for manual scoring, not automatic proof the model is wrong; retune expectations only when the unexpected finding is valid and post-worthy.

Persist manual scoring in a JSONL ledger so posting and graph-readiness evidence cannot get lost between runs:

```bash
# Single-variant quality scoring.
hermes pr-review dogfood-score /tmp/run.json \
  --case small-docs-fastapi-15815 \
  --quality artifact_only \
  --safe-to-post n/a \
  --notes "Clean docs control; zero findings/no noise."

# Paired baseline/graph scoring.
hermes pr-review dogfood-score /tmp/run.json \
  --case browser-tooling-playwright-41396 \
  --quality post_worthy \
  --bucket graph_missed_useful_baseline \
  --safe-to-post yes \
  --default-vote baseline_better \
  --notes "Graph caught the nested-controls issue but missed baseline's sibling/global grouping issue."

hermes pr-review dogfood-report evals/dogfood-runs/manual-scores.jsonl
```

The report stays conservative: repo-level findings-only posting should only move to canary after at least five quality-scored no-post cases, no `noise`/`miss` pattern, at least one `post_worthy` finding, and no truncated public posting. Graph default-on should only be considered after at least 12 scored records, no graph useful-miss/noise pattern, and at least 80% `graph_better`/`equivalent` votes. Until then, graph context should remain explicit opt-in.

For one-off review dogfood:

```bash
hermes pr-review review OWNER/REPO#123 --json
hermes pr-review review OWNER/REPO#123 --graph-context --local-repo /path/to/checkout --no-llm --json
hermes pr-review review OWNER/REPO#123 --graph-context-auto --local-repo /path/to/checkout --json
```

`--graph-context` is experimental and opt-in. It uses the CodeGraph CLI against an explicit `--local-repo` and writes `graph-context.json` / `graph-context.md` beside the normal artifacts. `--graph-context-auto` is intended for repo-level defaults: it requires an existing initialized `.codegraph/` index, uses a shorter sync timeout, and falls back to baseline if graph collection is unavailable. A trusted base-branch reviewer config may also set `graphContext: "auto"` or `graph_context: "auto"`. Graph collection must not install MCP config, run project code, or change posting behavior. CodeGraph creates/updates `.codegraph/` inside the checkout; that directory is the only allowed provider-side checkout mutation. The injected graph markdown is capped by `--max-graph-context-chars`; raw artifacts omit absolute checkout/binary paths.

Before enabling a repo-level graph default, inspect the checkout:

```bash
hermes pr-review graph-health --local-repo /path/to/checkout --json
hermes pr-review graph-health --local-repo /path/to/checkout --sync
```

A healthy checkout has a clean non-CodeGraph working tree, an existing initialized `.codegraph/` index, no pending/reindex signal from CodeGraph, and a successful optional sync.

## Watched repo auto-review

Use `watch-run` for the Greptile-style “enable these repos and review new PR heads automatically” loop. Polling works locally without a public webhook endpoint; `webhook-event` adds the event-driven entrypoint without changing Hermes core.

Posted comments are managed summaries, not only alert messages. A normal comment includes merge readiness, confidence, risk, finding count, summary, findings/no-findings state, important files changed, verification/context used, and a reviewed-commit footer. Keep `postFindingsOnly: true` while canarying noisy repos; switch to `postFindingsOnly: false` only when a repo is ready for always-post summary comments.

Local-only registry:

```json
{
  "repos": {
    "OWNER/REPO": {
      "enabled": true,
      "postComment": false,
      "graphContext": "auto",
      "graphContextBinary": "/absolute/path/to/codegraph",
      "localRepo": "/path/to/checkout"
    }
  }
}
```

Default paths:

```text
~/.hermes/pr-reviewer/repos.json
~/.hermes/pr-reviewer/watch-state.json
~/.hermes/pr-reviewer/webhook-secret
```

`hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout` is the preferred setup path. It creates or updates the registry entry, creates the webhook secret file if missing, leaves `postComment` disabled unless explicitly requested, and prints the exact GitHub webhook fields to paste. For a system service or other non-login environment, also pass `--graph-context-binary /absolute/path/to/codegraph`; the stored launcher path is used by both readiness checks and reviews, and its parent directory is prepended to the CodeGraph subprocess `PATH` for interpreter-backed launchers.

Before enabling posting, prove the local canary path with real PR open and synchronize events: enable the repo, run `watch-run --no-llm`, process signed or saved webhook payloads in no-post mode, then confirm `status` shows the reviewed head and delivery/state records. For the durable canary, also confirm the GitHub hook delivery log reports HTTP 202 from the Tailscale Funnel URL for both opened and synchronized PR heads.

Run it:

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout --webhook-url https://<this-host>.<tailnet>.ts.net/webhooks/github
hermes pr-review watch-run --no-llm --json
hermes pr-review watch-run --json
hermes pr-review watch-run --repo OWNER/REPO --force --json
hermes pr-review webhook-event --payload /tmp/github-pull-request.json --event pull_request --delivery DELIVERY_ID --json
hermes pr-review webhook-serve --host 127.0.0.1 --port 8787 --secret-file ~/.hermes/pr-reviewer/webhook-secret
hermes pr-review status --json
hermes pr-review webhook-status --github-repo OWNER/REPO --github-hook-id HOOK_ID --json
```

Tailscale Funnel pilot:

```bash
tailscale funnel --bg 8787
```

GitHub webhook settings:

- Payload URL: `https://<this-host>.<tailnet>.ts.net/webhooks/github`
- Content type: `application/json`
- Secret: same value used by `webhook-serve` (`--secret-file`, `--secret`, or `HERMES_PR_REVIEW_WEBHOOK_SECRET`)
- SSL verification: enabled
- Events: **Pull requests** only

`webhook-serve` behavior:

- binds to `127.0.0.1` by default so the public surface is the Tailscale Funnel URL, not a raw LAN listener;
- accepts `POST /webhooks/github` and `GET /healthz` only;
- verifies GitHub `X-Hub-Signature-256` before reading webhook semantics;
- caps body size, times out slow request reads, durably spools deliveries under `~/.hermes/pr-reviewer/deliveries/`, pre-queue ignores unsupported events/actions so low-value GitHub deliveries do not compete with active reviews, returns quickly after accepting normal webhook deliveries, serializes verified background review processing around the shared watch state, and returns `503 review_queue_busy` for supported review-triggering deliveries rather than admitting unbounded concurrent reviews;
- durably creates all delivery spool records, including pre-queue ignored events, without replacing an existing accepted or terminal delivery ID, fsyncing file content and published directory entries before acknowledgment; terminal duplicates return the preserved status, accepted duplicates resume the persisted body/event under the shared fence with `force=False`, and transient normal-worker lock/read setup errors retry with capped backoff;
- normal workers and startup recovery acquire the in-process slot before a stable receiver-wide advisory processing lock, preventing both lock-order inversion and overlapping receiver processes from reviewing or updating watch state concurrently across atomic spool replacement;
- on long-lived startup, binds localhost and freezes the historical spool candidate list before serving, then additionally locks each durable `accepted` spool JSON and replays it in the background through the normal head-deduplicating event path without inheriting `--force`; contended claims, transient lock/persistence errors, and unexpected event-path exceptions keep retrying with capped exponential backoff while ordinary returned event failures remain terminal, shutdown interrupts retry waits, stops the frozen batch between candidates, and joins any active recovery to completion, malformed accepted records become terminal failures, unreadable JSON is counted/logged for inspection, and `--once` does not scan historical records but does process an accepted duplicate explicitly delivered to that invocation after acquiring the same receiver-wide fence and rereading status;
- forwards reviewable verified request bodies plus `X-GitHub-Event` / `X-GitHub-Delivery` into `webhook-event`.

`webhook-event` behavior:

- accepts only GitHub `pull_request` payloads;
- reviews `opened`, `synchronize`, `reopened`, and `ready_for_review` actions;
- ignores disabled/unconfigured repos, PRs that are no longer open, stale payload heads that no longer match GitHub's current PR head, and unsupported actions without touching state;
- uses the same registry defaults, no-post safety, draft handling, lock, retry, and dedupe behavior as `watch-run`;
- records event metadata (`event`, `delivery`, `action`, repo, PR, head SHA) in watch state for reviewed/failed heads.

`status` / `webhook-status` behavior:

- reads the configured repo registry, webhook secret, watch state, recent delivery spool files, optional GitHub hook delivery rows, and optional receiver `/healthz` without mutating state;
- when `--github-repo OWNER/REPO --github-hook-id HOOK_ID` are provided, shells out to read-only `gh api repos/OWNER/REPO/hooks/HOOK_ID/deliveries -X GET -f per_page=N` and summarizes the latest event/action/status/code;
- treats missing first-run state/deliveries and receiver downtime as warnings, but invalid secret files/paths as failures;
- reports per-repo posting/graph settings plus last reviewed head so webhook/funnel problems are visible without manually inspecting `~/.hermes/pr-reviewer/*.json`;
- records the graph result and auto-fallback reason for completed watch/webhook reviews, reports a separate `graph_live` check, and warns when interactive graph readiness is healthy but the latest live review fell back;
- prints/returns `next_steps` so a WARN status points at the next setup or recovery command instead of leaving the operator to infer it.

Behavior:

- lists open PRs for enabled repos;
- skips drafts unless `reviewDrafts: true`;
- skips a PR if the same head SHA is already recorded in watch state;
- uses `graphContext: "auto"` and `localRepo` when configured;
- honors per-repo `postComment`, defaulting to no posting;
- defaults per-repo posting to findings-only with `postFindingsOnly: true`;
- always keeps `allow_truncated_post` false.

A Hermes cron can run the same command every 30-120 minutes once no-post watch runs look good.

Capture:

- finding count;
- risk;
- useful findings;
- false positives/noise;
- obvious misses;
- docs loaded;
- skipped files;
- truncation;
- artifact path.

Do not post to GitHub unless explicitly testing posting behavior. Posting a truncated-diff review is blocked by default; only override after manual artifact review:

```bash
hermes pr-review review OWNER/REPO#123 --post-comment
hermes pr-review review OWNER/REPO#123 --post-comment --post-findings-only
hermes pr-review review OWNER/REPO#123 --post-comment --allow-truncated-post
```

For webhook/watch canaries, prefer `postComment: true` with `postFindingsOnly: true` first. Clean zero-finding reviews still write local artifacts and watch state. They update an existing managed Hermes comment if one exists, but skip creating a new “looks good” comment. After the findings-only canary is quiet, graduate one repo at a time to `postFindingsOnly: false` so clean PRs create the full managed summary comment instead of staying silent.

### Posting canary gate

Posting behavior is tested separately from normal dogfood. Keep the default repo registry at `postComment: false` unless the current task is an explicit posting canary.

Use this checklist for every repository-specific posting canary:

1. Prove the repo is clean, receiver status is OK, and note the current posting mode.
2. Temporarily enable `--post-comment --post-findings-only` for the first posting canary.
3. For a clean PR, verify `findings: 0`, `comment.action: skipped`, `comment.reason: no_existing_comment`, and zero GitHub issue comments.
4. For a positive PR, first prove the finding in no-post mode, then enable posting and verify exactly one managed Hermes comment with `<!-- hermes-pr-review:summary:v1 -->`.
5. Push a fix/removal and verify the same managed comment updates to `Findings: 0` instead of leaving stale findings.
6. Run `watch-run --json` and verify the reviewed head dedupes as `already_reviewed`.
7. Once findings-only posting is proven, test `--post-comment --no-post-findings-only` on a clean PR and verify it creates exactly one managed summary comment with `Findings: 0`.
8. Reset or keep the graduated repo in always-post mode intentionally; do not leave other repos in posting mode by accident.

### Quality beta gate

Posting mechanics are necessary but not sufficient. Before enabling findings-only posting for normal repo work, use `evals/dogfood-runs/beta-matrix.md` to score no-post runs. Graduate a repo only when recent reviews are mostly post-worthy or artifact-only, with no repeating false-positive/missed-issue pattern.

## Product-boundary rule

Default target is external/local-first:

```text
private plugin → dogfood → harden → public/external plugin → possible upstream opt-in proposal
```

Do not reopen an upstream Hermes PR until:

- local dogfood has useful evidence across several PR types;
- no-post default is repeatedly verified;
- docs are installation-first and experimental;
- plugin is not default-enabled;
- reviewer output is low-noise;
- posting behavior is author-scoped and duplicate-safe;
- a review specifically checks upstream fit.

## Docs policy

`README.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`, and `docs/WORKFLOW.md` are useful only if they stay short and current. Delete or shrink them if they become stale architecture theater.
