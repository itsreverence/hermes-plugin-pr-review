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

## Verify a manual revision

1. Follow [the manual procedure](../skills/hermes-pr-review/SKILL.md) using fresh
   private state. Preserve reports and exact commit bindings.
2. Exercise a quiet real PR and a positive-finding case. Independently check the
   findings. Confirm deduplication and honest incomplete outcomes.
3. For implementation or bundle changes, run an independent review and repair
   blocking findings with regression tests. Rerun the focused and full checks.
4. For documentation-only changes, inspect the full documentation diff and
   validate local links. Independent implementation review is required when code
   or the skill bundle changes, not a claim to attach to a self-reviewed doc edit.
5. After publication is explicitly authorized, update the existing pull request.
   Read hosted CI for its exact head. Never transfer a previous green result to a
   new commit. Local readiness does not authorize push, merge, or release tagging.
6. Follow [pinned installation](SKILL_FIRST_INSTALL.md). Verify the installed
   bytes and a real installed-helper canary.

For documentation-only revisions with byte-identical bundles, repeat isolated
installation from the existing published bundle pin and record equality with the
new tree. Keep prior canary receipts tied to their original attempts. Do not
manufacture a fresh review completion merely to refresh a release note.

## Keep operation boundaries separate

- [Candidate scope](SKILL_FIRST.md): manual operation and experimental helpers.
- [Current readiness](SKILL_FIRST_READINESS.md): supported path, exact-revision
  proof, historical experiment decisions, and remaining publication gates.
- [Rollout gates](SKILL_FIRST_ROLLOUT.md): scheduling, unattended ownership, and
  legacy retirement. A manual release does not pass these gates automatically.
- [Source-enriched evidence](SKILL_FIRST_V02_VERIFICATION.md): current review proof.
- [Collection compatibility](SKILL_FIRST_COLLECTION_COMPATIBILITY.md): literal Git paths, bounded document retries, and same-target verification.
- [Dependency verification](SKILL_FIRST_DEPENDENCY_VERIFICATION.md): installed dependency collection, live review, and retrospective result reuse.
- [Blinded quality trial](SKILL_FIRST_QUALITY_TRIAL.md): archived-packet judgment repeatability, scoring, and limitations.
- [Legacy operations](OPERATIONS.md): existing plugin, receiver, and recovery.
- [Release policy](RELEASING.md): public release conventions.

Keep project-specific receipts in the repository documentation and private run
artifacts, not global agent memory. Never commit credentials or raw private PR
packets.
