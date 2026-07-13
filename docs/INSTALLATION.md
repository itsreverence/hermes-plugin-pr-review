# Installation

## Requirements

- Hermes Agent with third-party plugin installation
- Git and authenticated GitHub CLI (`gh`)
- A local checkout of each repository enabled for watched/webhook review
- Linux user systemd for managed service installation
- Tailscale Funnel or another public HTTPS reverse proxy for GitHub webhooks

CodeGraph is optional. Baseline reviews work without Node/npm or a graph index.

## Install the plugin

```bash
hermes plugins install itsreverence/hermes-plugin-pr-review/plugins/pr_review --enable
hermes pr-review doctor
```

Reinstall the public identifier with `--force --enable` to update a nested plugin install.

## First direct review

```bash
hermes pr-review review OWNER/REPO#123 --json
```

Posting is off unless `--post-comment` is explicitly supplied.

## Enable a repository

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/checkout
```

This creates or updates the local registry, creates or reuses a mode-`0600` webhook secret, and leaves `postComment` disabled.

Optional graph-backed auto mode:

```bash
hermes pr-review graph-setup --local-repo /path/to/checkout --install-missing
hermes pr-review enable OWNER/REPO \
  --local-repo /path/to/checkout \
  --graph-context auto \
  --graph-context-binary codegraph
```

## Install the managed receiver

Linux user-systemd path:

```bash
hermes pr-review service install
hermes pr-review service status
```

The managed service binds to loopback, pins the active Hermes profile home, and never installs a root unit. Other platforms may run `webhook-serve` under their own process manager.

## Configure Tailscale Funnel

Plan without changing Tailscale:

```bash
hermes pr-review funnel setup
```

Review the plan, then apply:

```bash
hermes pr-review funnel setup --apply
```

The command refuses to replace an unrelated route, discovers the public hostname, and verifies `/healthz`. If using another HTTPS proxy, expose `http://127.0.0.1:8787` and route `/webhooks/github` to it.

## Create the GitHub webhook

Plan mode performs no remote mutation:

```bash
hermes pr-review webhook setup OWNER/REPO \
  --url https://YOUR_HOST/webhooks/github
```

Apply only after reviewing the plan:

```bash
hermes pr-review webhook setup OWNER/REPO \
  --url https://YOUR_HOST/webhooks/github \
  --apply
```

The hook uses JSON, verifies TLS, subscribes only to pull-request events, and receives the secret through `gh api` stdin rather than command arguments or normal output.

## Verify

```bash
hermes pr-review doctor
hermes pr-review service status
hermes pr-review funnel status --verify
hermes pr-review webhook status OWNER/REPO
hermes pr-review status --github-repo OWNER/REPO --github-hook-id HOOK_ID
```

Open or synchronize a low-risk pull request. Confirm GitHub records HTTP 202, the accepted delivery is processed, the exact head has local artifacts, and no comment was posted. See [TESTING.md](TESTING.md) before enabling posting.
