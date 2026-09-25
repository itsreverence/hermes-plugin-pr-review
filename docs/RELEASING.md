# Releasing

## Manual skill-first scope

The legacy plugin release policy below does not define the manual skill's
activation boundary. The standalone candidate uses Python helpers directly,
without the plugin launcher, receiver, or scheduler. Its evidence is recorded in
[dependency verification](SKILL_FIRST_DEPENDENCY_VERIFICATION.md) and the
[blinded quality trial](SKILL_FIRST_QUALITY_TRIAL.md).

A decision that the candidate is suitable for supervised manual use does not
merge its draft PR, create a release tag, enable unattended reviews, authorize
GitHub posting, or retire the legacy path. Those remain separate decisions under
[the rollout gates](SKILL_FIRST_ROLLOUT.md).

The [readiness record](SKILL_FIRST_READINESS.md) names the exercised manual bundle
and current decisions. For a documentation-only release-readiness change, keep
the bundle unchanged, rerun local gates, and verify byte equality with the pinned
installation. Publishing the resulting commit and checking its hosted CI remain
separate steps. A prior green run does not cover a new commit.

## Legacy plugin release status

At the recorded plugin checkpoint, Hermes PR Review was an unreleased public
beta distributed from `main`, with a planned `0.2.0` release and no stable tag.
The manual-readiness pass does not revalidate legacy release status.

The recorded blocker was shell-visible CLI exit-status propagation in supported
Hermes runtimes, tracked in [issue #1](https://github.com/itsreverence/hermes-plugin-pr-review/issues/1)
and [NousResearch/hermes-agent#43645](https://github.com/NousResearch/hermes-agent/pull/43645).
Recheck both and the actual supported runtime before a legacy release. This
plugin-launcher issue is not a blocker for direct manual Python helpers.

## Previously proven legacy public-beta properties

- anonymous clone and nested-plugin install in an isolated Hermes profile;
- CLI discovery, prerequisite diagnosis, and uninstall;
- Python 3.11/3.12 CI against a pinned Hermes API checkout;
- no-post webhook opened/synchronize delivery, exact-head artifacts, and deduplication;
- force-reinstall update, detached public-commit rollback, restore, and uninstall while preserving reviewer state;
- clean public snapshot and credential scans;
- MIT licensing and private vulnerability reporting.

These properties describe prior evidence, not a permanent guarantee. Rerun release gates against the exact release tree.

## Prepare legacy `v0.2.0`

1. Confirm the upstream exit-status fix is merged and available in the minimum supported Hermes runtime.
2. Verify `doctor` returns nonzero for required failures through every supported launcher.
3. Update the supported Hermes version/API pin and compatibility documentation.
4. Confirm plugin metadata and `CHANGELOG.md` use `0.2.0` and convert the changelog heading from Unreleased to a dated release.
5. Run the complete local gate in [TESTING.md](TESTING.md).
6. Run a clean-tree credential/private-marker scan.
7. Verify anonymous install, enablement, CLI help, doctor, update, rollback, restore, and uninstall in an isolated Hermes home with normal GitHub credentials unset.
8. Prove a real signed no-post webhook delivery against the exact release commit.
9. Obtain an independent full-range review.
10. Wait for required CI on the release commit.

## Publish and verify

1. Tag the exact reviewed commit as `v0.2.0`.
2. Create a GitHub release with user-facing changes, requirements, known limitations, and upgrade/rollback guidance.
3. Verify the public tag archive and nested plugin path.
4. Install from the tagged source in a fresh isolated profile.
5. Re-read the GitHub release, tag commit, CI, and repository security/ruleset state.

Do not tag merely because current `main` installs successfully. Public install readiness and stable release readiness are separate claims.
