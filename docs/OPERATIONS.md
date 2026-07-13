# Operations

## Routine status

```bash
hermes pr-review doctor --json
hermes pr-review service status --json
hermes pr-review service logs --lines 100
hermes pr-review funnel status --verify --json
hermes pr-review webhook status OWNER/REPO
hermes pr-review status --github-repo OWNER/REPO --github-hook-id HOOK_ID
```

`status` reads the local registry, secret-file safety, receiver health, watch state, recent delivery spool, graph readiness, and optional GitHub delivery rows without mutating them. WARN/FAIL output includes next steps.

## Local state

```text
~/.hermes/pr-reviewer/repos.json
~/.hermes/pr-reviewer/watch-state.json
~/.hermes/pr-reviewer/webhook-secret
~/.hermes/pr-reviewer/deliveries/
~/.hermes/pr-reviewer/reviews/
```

Treat this directory as private. It may contain repository metadata, webhook payloads, diagnostics, and review content.

## Posting policy

- `postComment` defaults to `false`.
- `postFindingsOnly` defaults to `true` when posting is enabled.
- watched/webhook reviews never override truncated-diff posting protection.
- direct `--allow-truncated-post` is for manual artifact review only.

Use no-post review artifacts before changing posting policy. Managed comments are author-scoped and update rather than accumulating duplicates.

## Receiver and recovery behavior

The receiver binds to `127.0.0.1`, accepts the webhook path and health endpoint, verifies GitHub HMAC signatures, limits body size/read time, and durably spools accepted deliveries before returning. Processing is bounded and serialized around shared watch state. Startup recovery replays stranded accepted deliveries through the normal head-deduplicating path.

Unsupported events/actions are ignored before review admission. Busy review-triggering requests receive a retryable `503` rather than creating unbounded work. Malformed accepted records become inspectable terminal failures; transient processing/persistence failures retry with capped backoff.

## Update

```bash
hermes plugins install \
  itsreverence/hermes-plugin-pr-review/plugins/pr_review \
  --force --enable
hermes pr-review doctor
hermes pr-review service restart
```

## Roll back to a known commit

```bash
git clone https://github.com/itsreverence/hermes-plugin-pr-review.git \
  /tmp/hermes-plugin-pr-review-rollback
git -C /tmp/hermes-plugin-pr-review-rollback checkout --detach COMMIT_SHA
hermes plugins install \
  "file:///tmp/hermes-plugin-pr-review-rollback#plugins/pr_review" \
  --force --enable
```

This replaces only the installed plugin. Registry, secret, delivery spool, and review artifacts remain outside the plugin directory. Reinstall the public identifier to return to current `main`.

## Remove

```bash
hermes pr-review webhook remove OWNER/REPO --hook-id HOOK_ID --apply
hermes pr-review disable OWNER/REPO --apply
hermes pr-review service remove --apply
hermes plugins remove pr-review
```

Service removal preserves secrets and artifacts. There is intentionally no automated device-wide Funnel reset. Inspect `tailscale funnel status` and remove only the reviewer route with the scoped operation supported by the installed Tailscale version.

## Rotate a potentially exposed webhook secret

1. Disable or remove the affected GitHub hook.
2. Replace the local secret with mode `0600`.
3. Update or recreate the hook with the new secret.
4. Restart the receiver.
5. Verify a signed no-post delivery before re-enabling normal events.
