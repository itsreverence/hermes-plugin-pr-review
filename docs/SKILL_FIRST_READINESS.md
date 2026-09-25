# Manual skill readiness

## Decision

The supported path on this branch is **supervised, single-pass manual review**
through `hermes-pr-review`. The installed helpers have completed real bounded
reviews with exact ledger and report read-back. This supports practical manual
use, not a detection-accuracy claim, merge approval, or unattended operation.

This record closes the conversion's experimental phase. Historical proposals
below are not a queue of required follow-up experiments. Publication of this
readiness documentation, merging the draft PR, tagging a release, and legacy
cutover are separate decisions.

## Exercised bundle

The implementation pin is `eab3b8233be256ad53dbebfb08b7253895521dcc` in
[draft PR #11](https://github.com/itsreverence/hermes-plugin-pr-review/pull/11).
It retains the baseline review rubric and adds the
[clearer scoped report renderer](SKILL_FIRST_REPORT_PRESENTATION.md).
This readiness change edits documentation only, not that bundle or its tests.

On 2026-09-25 UTC, read-only GitHub checks confirmed both hosted Python jobs
passed for that exact pin in
[run 35800642936](https://github.com/itsreverence/hermes-plugin-pr-review/actions/runs/35800642936).
The PR was open and draft at the read-back. Those results do not cover a later
unpublished documentation commit.

A fresh installation through the full-commit raw URL passed the normal Hermes
skill scanner with verdict `SAFE`, without `--force`. All 13 installed files
matched the published Git blobs, the branch's bundle, and the active installed
bundle. The isolated installed helper's `status` command ran outside the checkout
and returned an empty attempt list in fresh private state. The active installation
was not replaced. See [pinned installation and rollback](SKILL_FIRST_INSTALL.md).

Fresh local verification used the unchanged implementation and the CI-pinned
Hermes API source `7acaff5ef2bcbaa22bd23b72efe60906123a4f55`:

- Python 3.11: 754 tests and 82 subtests passed, with no skips reported.
- Python 3.12: 754 tests and 82 subtests passed, with no skips reported.
- Ruff 0.15.10: passed.

Commands and dependency pins are in [WORKFLOW.md](WORKFLOW.md). These tests execute
this trusted reviewer implementation, not any target PR. Installation equality
and an empty-state smoke are not new model judgments or fresh review completions.
Private receipts retain commands, logs, exact file hashes, and pre-edit backups.

## Latest real foreground review

The installed manual workflow reviewed
[Hermes #121939](https://github.com/NousResearch/hermes-agent/pull/121939), a
webhook route-configuration change, on 2026-09-24 UTC:

- Head: `8121a895a272b6dbfe61aa329850bcbcd5780e3d`.
- Base: `749220ef0007f8d87bd1531f1c24b0fe93816385`.
- Merge base: `e8acdc5fc899d7e9220dd6ac0dfeedebb4a42a9a`.
- Attempt: `f744064d822c4aa5864d9c8822100797`.
- Evidence: two patches, three source-side records, and four trusted-base documents.
- Outcome: completed bounded static assessment, with no supported introduced
  defect established. The exact ledger, `result.json`, and `review.md` were read back.

The first attempt stopped at the default documentation budget. After inspecting
the required guidance size, a new attempt used an explicit 150000-byte document
cap for 123069 bytes of guidance. The original hold remains preserved. Full reads
were recorded, with byte-identical source ranges reused and equality checked.
Hash and coverage checks establish consistency, not semantic understanding.

The review found the local null/list normalization, collision suffixing, and
valid-mapping handling consistent with the included source. Added tests were
inspected, not run. This does not verify live webhook delivery, the author's
incident report, gateway-wide failure isolation, or merge readiness. The quiet
result is not evidence of comprehensive defect detection. Nothing was posted.

Earlier positive findings, result reuse, and missing-evidence outcomes remain in
[source-enriched verification](SKILL_FIRST_V02_VERIFICATION.md) and
[dependency verification](SKILL_FIRST_DEPENDENCY_VERIFICATION.md). Their original
revision bindings and limitations remain intact.

## Experiment dispositions

- [Archived quality trial](SKILL_FIRST_QUALITY_TRIAL.md): useful supervised findings,
  but known misses on a selected sample. Not population accuracy or human ground truth.
- [Reference research](SKILL_FIRST_REVIEW_REFERENCES.md): communication ideas from
  public reviews, not a live comparison against other review products.
- [Guidance comparison](SKILL_FIRST_GUIDANCE_COMPARISON.md): no demonstrated gain;
  retain baseline guidance. Its earlier presentation hold was resolved separately.
- [Presentation comparison](SKILL_FIRST_PRESENTATION_TRIAL.md): model readers
  preferred some headings, but exposed ambiguous completion and quiet wording.
  The later renderer revision addressed those issues. Preference results apply
  only to the original frozen layouts, not to the revised renderer.
- [Second-pass trial](SKILL_FIRST_SECOND_PASS_TRIAL.md): no independently supported
  additional detection gain satisfying its gate. Do not adopt automatic second passes.
- **Version-bound evidence comparison:** closed as infrastructure-incomplete and
  unscored. Background approval-delivery failures prevented completed independent
  arms. Genuine approval timeouts and missing-notifier pending states remain
  distinct recorded failures. No quality score or rollout decision follows from them.

A separately frozen, informed foreground investigation used pinned HTTPX 0.28.1
source to assess the disputed worker-lifetime claim from historical Hermes #118840.
It supported conditional ownership escape and a later closed-client fetch failure.
The in-flight empty-cache trigger remained uncertain because HTTPcore lifecycle
semantics were absent. Direct permanent poisoning of unrelated later parent
clients was contradicted for that mechanism, not all shared disk-cache effects.

That investigation read focused ranges, not the entire enriched packet. Its
consistency checker validated 39 citations and rejected fabricated text and
missing read coverage. It did not execute dependency code, complete an independent
arm, improve measured reviewer accuracy, or repair background approval routing.
Frozen evidence and failed attempts remain private and preserved. No retry of
that study is scheduled or required for manual use.

## Remaining boundaries

The `judge` and shadow-scanner helpers remain experimental. No scheduled worker,
automatic second pass, credential restriction, GitHub publication, or legacy
service replacement is authorized by this record. The
[unattended and cutover gates](SKILL_FIRST_ROLLOUT.md) remain separate and unpassed
by this closeout. The surrounding Hermes session is not a sandbox.

The next delivery step is publication of the reviewed documentation commit to the
existing draft PR, once explicitly authorized, followed by exact-head hosted CI.
No further reviewer experiment is a prerequisite. A new implementation or bundle
change would require its own tests, independent review, and installation proof.
