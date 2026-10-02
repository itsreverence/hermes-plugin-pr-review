# Blinded archived-packet quality trial

> Historical selected-case evaluation, not a current accuracy claim. Later
> guidance, presentation, and second-pass decisions are indexed in
> [current readiness](SKILL_FIRST_READINESS.md). The proposed next experiment
> below is retained as history, not an outstanding task.

## Scope

This trial compares fresh static judgments with earlier parent-adjudicated
findings on three saved Hermes PR packets. It is a repeatability check on selected
known cases, not an accuracy benchmark on unseen changes. The reference findings
are static causal assessments, not runtime reproductions or maintainer verdicts.
The quiet control has no known supported finding. It is not proven defect-free.

The trusted installed reviewer bundle is commit
`a1c74b80f6496d0ae4d5d02df109042503ca3037`. All 13 installed files matched the
saved manifest before dispatch. The private contract, expected results, input
inventory, and SHA-256 freeze were saved before the reviewers ran.

Each fresh reviewer received only its exact packet, trusted review instructions,
result schema, and private output paths. Expected findings, expected counts,
selection classes, earlier judgments, and sibling cases were withheld. PR
metadata remained visible. This is expected-verdict blinding, not anonymization,
independent model-family validation, or OS sandboxing.

Reference judgments and fresh reviewers use session-reported `gpt-6-astra` via
`openai-codex`. A fresh context prevents deliberate result seeding but does not
eliminate correlated errors from the same model family. Model identity is not
independently provider-attested.

## Frozen cases

### Case A: scratch worktree handling, Hermes #118824

- Head: `efdd27a84572cd92599eadf8bc9d01da332679aa`.
- Base and merge base: `ee8a919fd2769166d45ebf67f45ff5b1acec69fe`.
- Evidence: three base documents, two patches, four full source records.
- Serialized `input.json`: 158949 bytes. Rendered context: 162526 bytes.
- Reference: three static causal issues, including a cleanup exception and two
  defects in the new regression fixture.

### Case B: reasoning toggle, Hermes #118879

- Head: `66f51f54ec34e18e24e91c6e75895d8e5f697ddd`.
- Base: `75e9567ca757a427ca4bd2ae12add9b53846dc06`.
- Merge base: `77f80d31eeb9907cfc8e441f1b66f4704179eacc`.
- Evidence: four base documents, two patches, three full source records.
- Serialized `input.json`: 151633 bytes. Rendered context: 152926 bytes.
- Reference: no supported introduced defect established in the earlier review.

### Case C: HTTP client reuse, Hermes #118840

- Head: `d2e7e95b213a3610319fe9af920e34e28648f14d`.
- Base and merge base: `ee8a919fd2769166d45ebf67f45ff5b1acec69fe`.
- Evidence: five base documents, six patches, 24 full source records.
- Serialized `input.json`: 772863 bytes. Rendered context: 843634 bytes.
- Reference: one static DNS-error fallback regression whose causal argument
  requires the explicitly collected dependency sources.

The source packets for A and B were collected with an earlier bundle revision.
They were copied byte-for-byte, not relabeled as collections by the new bundle.
All judgments use the current trusted rubric. This does not exercise new
collection or current-head finalization for those packets.

## Scoring and operational boundaries

The parent validates each strict result and patch quote through the trusted
helper, then separately adjudicates the causal claim. Matching is by causal
issue rather than title or finding-object count. One finding may cover more than
one reference issue. Duplicate descriptions do not create additional discoveries.
New supported issues are recorded separately without changing the frozen reference.

Coverage, known issues recovered, misses, unsupported warnings, evidence size,
and elapsed time are reported separately. Approval or provider failures are
harness failures, not evidence that a reviewer missed a bug. A failed attempt
remains visible even if a separately authorized retry succeeds.

Replay judgments belong to a private evaluation ledger. They are not finalized
into historical completed or expired workflow attempts. The installed live
repeat/reuse check is a separate result in
[dependency verification](SKILL_FIRST_DEPENDENCY_VERIFICATION.md).

No target code, snippets, imports, tests, or packages may execute. Reviewers may
read the saved evidence and write their private results. Network, credential,
GitHub-write, schedule, and service operations are outside their authority.
Tool traces and preserved hashes support containment checks, but do not prove
process isolation. Raw packets and model outputs remain private.

## Adjudicated results

### Case A: one supported warning, two known issues missed

The fresh reviewer completed its bounded static assessment and returned one
warning. Parent inspection accepted the warning about rebasing an already-relative
`gitdir` from the test runner's working directory. The exact patch citation and
strict result schema passed the installed validator.

Two frozen reference issues were absent from the judgment:

- The newly added `Path.resolve()` can raise an uncaught `RuntimeError` on a
  symlink loop on supported Python 3.11/3.12. Both surrounding handlers catch
  only `OSError`, so the exception can interrupt cleanup before deletion.
- The fixture string contains two backslashes before `n`, writing literal
  backslash-n into the gitdir value rather than a newline. Correcting the
  relative-path calculation alone does not fix that defect.

The parent rechecked the pinned source and patch. A separate lookup of the
[Python 3.11 path contract](https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.resolve)
confirmed the documented loop exception. The fixture's source characters were
checked as data, not executed. These checks corroborate the original static
reference; they are not discoveries credited to the fresh reviewer.

An informed independent challenger also retained both missed issues after reading
the pinned source. The exception claim is limited to the local cleanup call, not
an unobserved application crash. The newline defect is a POSIX fixture-validity
problem: the test can still exercise relative-path discovery, but its assertions
do not establish that the rewritten worktree pointer was valid before deletion.
Neither challenge was a runtime reproduction or another blind trial judgment.

The case receives the project's `miss` quality label despite producing a useful
warning. The retained warning is `useful_but_edit`: its fixture failure argument
is static and must not read like an executed test result. No unsupported warning
was established. Self-recorded packet-inspection time was about 408 seconds,
excluding dispatch and final artifact overhead.

### Case B: quiet control remained quiet

The fresh reviewer completed the bounded assessment with no findings. Parent
inspection agreed that the changed local behavior and supplied tests are
consistent. The strict result schema passed. This is `artifact_only` evidence,
not a reason to publish an all-clear comment or claim live-provider correctness.
Self-recorded packet-inspection time was about 305 seconds, excluding dispatch
and final artifact overhead.

### Case C: first attempt held at an approval boundary

The first reviewer read all five documents, six patches, and one complete source
record before an approval timeout blocked the next local read. It returned an
explicit incomplete judgment rather than a clean review. That attempt remains
preserved and is not scored as a quality miss. Fresh operator consent authorized
one replacement reviewer on byte-identical evidence with separate output files.

The replacement completed the bounded assessment and recovered the frozen
DNS-error fallback issue. Parent adjudication accepted the warning. Strict schema
and patch-citation validation passed. All 35 evidence-unit inventory entries
matched their source hashes and declared complete read ranges, including six
byte-identical dependency pairs read once with both identities preserved.

This judgment receives the `post_worthy` quality label for its actionable static
finding. The label does not authorize posting. No unsupported or additional
finding was established. The replacement's self-recorded packet-inspection time
was about 478 seconds, excluding dispatch and final artifact overhead. The first
attempt's approximately 369 seconds remain separate failed-attempt overhead.

## Aggregate and readiness decision

Across three selected cases, the fresh completed judgments recovered two of four
frozen reference issues. Two issues were missed in case A. The control produced
no warning, and no unsupported warning was established in either positive case.
There were four blinded reviewer attempts: three complete static assessments and one
approval-blocked incomplete attempt, followed by its authorized replacement.
These are offline evaluation outcomes, not four official live review completions.

This is not a population recall estimate. Cases were selected using earlier
results, the reference is static and model-assisted, and all reviewers use the
same model family. Hashes and complete read inventories verify evidence
consistency, not reasoning quality. Case A demonstrates that distinction.

The installed manual workflow has passed its operational collection, result,
read-back, preservation, and repeat/reuse checks. It remains useful as a
**supervised review aid**, with findings independently checked. This trial does
not support treating a single complete assessment as a comprehensive review or
promoting the candidate to unattended operation. Draft PR #11 remains a draft;
no merge, release tag, GitHub publication, or legacy cutover is part of this work.

The next justified change is a narrow review-guidance experiment: check new
exception types against surrounding handlers and supported runtime versions,
and check that regression fixtures describe valid inputs rather than merely
satisfying weak assertions. Test any guidance change on unseen held-out cases
before claiming an improvement. Do not tune to these answers, raise collection
limits, drop required evidence, or add automation to compensate for missed issues.

## Verification boundary

Fresh offline checks of the unchanged reviewer implementation returned 734 tests
and 82 subtests passed on each Python 3.11 and 3.12. Ruff 0.15.10 and whitespace
checks passed. The implementation commit's two hosted jobs were read back green.
Those tests exercise the reviewer, not these target PRs.

An independent intermediate documentation audit found no blocking inconsistency.
Its suggestions led to an explicit same-model disclosure and a saved comparison
of old/new live-reuse metadata. The final quality scores are parent-adjudicated,
not covered by that earlier documentation-audit verdict.

The installed bundle, rollback copy, frozen reference, and packet hashes remain
separate from new evaluation outputs. Observed reviewer tool traces contain
local evidence processing and output writes, not target execution or publication.
The trace format abbreviates long arguments, so this is not a syscall-level audit
or proof of sandboxing. No schedules, services, or credentials changed.
