# Development and verification workflow

## Run local checks

From this trusted checkout, point `HERMES_AGENT_SRC` at a compatible Hermes
source tree. Use fresh private HERMES_HOME for tests that load the plugin.

```bash
HERMES_HOME=$(mktemp -d) PYTHONPATH="$PWD:$HERMES_AGENT_SRC" uv run --no-project --python 3.11 --with pytest --with pyyaml --with openai==3.17.0 --with httpx==0.28.1 python -m pytest tests/plugins tests/skill_first -q -p no:cacheprovider
uv run --no-project --python 3.11 --with 'ruff==0.15.10' ruff check --isolated --no-cache plugins tests scripts skills
git diff --check
```

Repeat the test command with Python 3.12 to match CI. The manual candidate tests
run independently without Hermes imports. Optional transport integration tests
also need the installed Hermes parser and SDK; report skips separately from
executed integration tests.

Do not execute code from the PR being reviewed. These commands test this trusted
reviewer implementation, not a target PR.

## Review and publish a manual candidate

1. Follow [the manual procedure](../skills/hermes-pr-review/SKILL.md) using fresh
   private state. Preserve reports and exact commit bindings.
2. Exercise a quiet real PR and a positive-finding case. Independently check the
   findings. Confirm deduplication and honest incomplete outcomes.
3. Run an independent implementation review and repair blocking findings with
   regression tests. Rerun the focused and full checks.
4. Publish the reviewed branch as a pull request. Read hosted CI results for its
   exact head commit before installation.
5. Follow [pinned installation](SKILL_FIRST_INSTALL.md). Verify the installed
   bytes and a real installed-helper canary.

## Keep operation boundaries separate

- [Candidate scope](SKILL_FIRST.md): manual operation and experimental helpers.
- [Rollout gates](SKILL_FIRST_ROLLOUT.md): scheduling, unattended ownership, and
  legacy retirement. A manual release does not pass these gates automatically.
- [Source-enriched evidence](SKILL_FIRST_V02_VERIFICATION.md): current review proof.
- [Legacy operations](OPERATIONS.md): existing plugin, receiver, and recovery.
- [Release policy](RELEASING.md): public release conventions.

Keep project-specific receipts in the repository documentation and private run
artifacts, not global agent memory. Never commit credentials or raw private PR
packets.
