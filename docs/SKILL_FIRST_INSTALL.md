# Install the reviewed manual skill

Install only after the manual evidence, independent review, and hosted CI gates
in [the rollout record](SKILL_FIRST_ROLLOUT.md) pass. Installation does not enable
polling or retire the legacy plugin.

## Pin the bundle

Use the full published commit SHA, not a branch URL. The direct-URL installer
fetches only supporting paths named in SKILL.md. Its bundle inventory therefore
lists every required Python module and reference.

```bash
hermes skills install https://raw.githubusercontent.com/OWNER/REPO/COMMIT/skills/hermes-pr-review/SKILL.md --category software-development --yes
```

Replace OWNER, REPO, and COMMIT with the reviewed publication. First exercise
this command with a fresh temporary HERMES_HOME. Do not supply `--force` to bypass
a blocked scanner verdict. Inspect any findings before proceeding.

## Verify the installed copy

Compare the exact installed file inventory and SHA-256 of every file with the
published commit's bundle. Ignore only generated Python bytecode. A successful
installer exit does not prove that all support files arrived.

Load the installed skill through `skill_view(name='hermes-pr-review')`. Run its
helper from outside the repository against fresh private state. Prepare, assess,
and finalize a bounded real PR, then read back the exact report and ledger row.
Repeat prepare to verify that the installed bundle reuses the completed result.
Do not use the legacy `pr-reviewer` state directory.

Keep an owner-private receipt containing the source commit, bundle file hashes,
installation path, canary attempt IDs, and outcomes. No raw PR packets belong in
this public repository.

## Roll back

Before installation, confirm that no existing skill with this name would be
overwritten. If replacing an existing copy, preserve it and its installation
metadata first. Stop invoking the new skill to disable manual use. Use the normal
Hermes skill uninstaller for a hub-installed copy, or restore the exact backup.
Preserve review state and reports. Do not alter legacy hooks, services, or routing
as part of skill rollback.
