# Contributing to Hermes PR Review

Thanks for helping improve Hermes PR Review. Focused bug fixes, tests, documentation, compatibility updates, and carefully scoped feature proposals are welcome.

## Before you start

- Search existing issues before opening a new one.
- Discuss substantial product, trust-boundary, or posting changes in an issue first.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
- Never include webhook secrets, provider credentials, private repository content, raw webhook payloads, or unredacted review artifacts.

## Development setup

This repository currently tests against a pinned Hermes Agent API checkout. Point `HERMES_AGENT_SRC` at a compatible local Hermes checkout:

```bash
export HERMES_AGENT_SRC=/path/to/hermes-agent
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m pytest tests/plugins tests/skill_first -q
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m py_compile plugins/pr_review/*.py
ruff check plugins tests scripts skills
git diff --check
```

Use Ruff 0.15.10 to match CI. The manual skill can be tested independently
with `python -m pytest tests/skill_first -q`; it does not require Hermes imports.
See [manual scope](docs/SKILL_FIRST.md) before any installation. Use the isolated
test environments and pinned dependency commands in [WORKFLOW.md](docs/WORKFLOW.md).

For a development installation of the legacy plugin in the active Hermes profile:

```bash
./scripts/install-dev.sh
hermes pr-review doctor
```

The installer creates a symlink to this checkout. This is legacy-plugin setup,
not skill installation. Manual-skill users follow
[pinned installation](docs/SKILL_FIRST_INSTALL.md). Existing plugin users follow
[legacy installation](docs/INSTALLATION.md).

## Pull requests

Keep changes narrowly scoped and explain:

- the user-visible problem or benefit;
- trust, posting, webhook, local-artifact, or compatibility implications;
- tests added or updated;
- exact verification commands and any live no-post canary performed.

Do not execute pull-request code during review or tests by default. Keep posting opt-in, load configuration from the trusted base branch, and avoid maintainer-specific paths or defaults.

See [docs/TESTING.md](docs/TESTING.md) for dogfood and canary expectations. Maintainers use [docs/RELEASING.md](docs/RELEASING.md) for publication.
