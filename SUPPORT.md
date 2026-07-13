# Support

## First checks

```bash
hermes pr-review doctor --json
hermes pr-review service status --json
hermes pr-review funnel status --verify --json
hermes pr-review webhook status OWNER/REPO
```

Start with the smallest relevant diagnostic. Service journals, webhook deliveries, and review artifacts may contain private repository metadata or review content; inspect and redact them before sharing.

## Bugs and feature requests

Use the repository's [issue forms](https://github.com/itsreverence/hermes-plugin-pr-review/issues/new/choose). Include the Hermes version, plugin revision, platform/process-manager shape, clear reproduction steps, and only relevant redacted output.

Questions about installation and operation should first use:

- [Installation](docs/INSTALLATION.md)
- [Operations](docs/OPERATIONS.md)
- [Architecture](docs/ARCHITECTURE.md)

## Security issues

Do not open a public issue containing webhook secrets, provider credentials, private repository data, raw webhook payloads, or review artifacts. Use the private reporting route in [SECURITY.md](SECURITY.md).

## Scope

This project is an external public-beta Hermes plugin, not a default-enabled Hermes core feature or hosted review service. General Hermes Agent installation/provider problems belong in the Hermes Agent support channels unless they reproduce specifically through this plugin.
