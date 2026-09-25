# Held-out review-guidance comparison

> Historical experiment record. Baseline guidance was retained. The presentation
> hold described here was later resolved by a separately authorized
> [presentation trial](SKILL_FIRST_PRESENTATION_TRIAL.md). See
> [current readiness](SKILL_FIRST_READINESS.md) rather than treating the final
> proposals below as active work.

## Decision

Retain the installed baseline rubric. The candidate did not demonstrate a useful
quality gain in this bounded comparison. No installed skill, production renderer,
model configuration, or schedule changed.

The planned presentation comparison remains blocked. Its local paired-rendering
step hit an approval timeout, and the request for fresh retry consent received
no response. The private renderer draft exists, but no paired layouts were
produced or reader preference measured. This is a partial closeout, not completion
of both experiments.

## Experiment design

The baseline was the unchanged installed review rubric. The candidate appended
four focused passes: error paths, regression-fixture validity, counterexamples,
and concise causal explanation. It did not change evidence budgets, result
schema, finding limits, or safety rules.

The contract and both guidance files were frozen before case selection. Cases
were selected from open, non-draft public PR metadata and file statistics. No
patches or review comments were read before selection. Selection was purposeful,
not random, and favored small Python changes in runtime, test infrastructure,
and dependency-contract behavior.

Each case was read by two fresh isolated reviewer contexts, one per guidance
condition. Both conditions received byte-identical collected packets. The six
reviewers were prohibited from seeing other outputs or reference assessments.
A separate reference reviewer inspected all three cases without experimental
outputs. All used session-reported `gpt-6-astra` through `openai-codex`; this is
not independent model-family validation or provider-attested identity.

The Greptile/ClawSweeper examples and earlier archived trial cases were excluded
from this guidance test. They remain development material. The presentation test
was intended to reuse the earlier judgments, with their technical content held
constant, but did not reach generation or evaluation.

## Cases and recorded outcomes

### D: HTTPX exception mapping

HTTPX #3778 replaces an exception-mapping context manager with cached helpers
and explicit handlers in one file, with 37 additions and 30 deletions.[1]

- Head: `09fc81a33c51f090002e340173f231d403c11ac5`.
- Base and merge base: `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`.
- Packet: one base document, one patch, and two full source records.
- Baseline: complete bounded assessment, zero findings.
- Candidate: complete bounded assessment, zero findings.
- Independent reference: no concrete introduced issue established.

This supplies a quiet outcome, not proof that the PR is defect-free. The reviewers
compared the supplied exception table, mapping specificity, chaining, and visible
call sites. No runtime equivalence or benchmark claim was verified.

### E: HTTPX test-server lifecycle

HTTPX #3775 adds exception capture, a startup deadline, and a timed thread join
to a test-server fixture, with 31 additions and four deletions.[2]

- Head: `8a959d830e8631f84401aeb5fd2e0ffa9bbfe3d7`.
- Base and merge base: `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`.
- Packet: one base document, one patch, and two full source records.
- Baseline: incomplete, zero findings.
- Candidate: incomplete, zero findings.
- Both identified missing Uvicorn lifecycle/version evidence as essential to
  resolving exception propagation and worker shutdown.
- The independent reference also required an incomplete outcome. It proposed a
  conditional diagnostic-loss concern for worker exceptions after startup.
  A separate informed challenger retained the narrow source-visible concern:
  an exception escaping `server.run()` after the startup observer exits is
  consumed by the new wrapper without restoring the old unhandled-thread path.

Neither arm returned that concern. The candidate's inspection notes explicitly
noticed the startup-only observer but withheld a finding because a concrete
post-start Uvicorn failure path was not established. The reference and challenger
accepted a narrower conditional boundary defect. This is a shared omission
relative to that static reference, not proof of a reproduced runtime bug or a
recall score for completed reviews. The unavailable dependency prevents stronger
claims about actual occurrence or a test suite silently passing. It does not
change either arm's incomplete coverage status.

### F: Requests exception translation

Requests #7471 adds a transport-level error translation and one long-hostname
test parameter, with 18 added lines across two files.[3]

- Head: `1c6974be38c819d39237df3224e0c88ee289541d`.
- Base and merge base: `cd90742ed94d901759e26766197d0ce7c7bd9c8e`.
- Packet: one base document, two patches, and four full source records.
- Baseline: incomplete, zero findings.
- Candidate: incomplete, zero findings.
- Independent reference: incomplete, no concrete introduced issue established.
- Missing context includes the relevant urllib3 exception and connection path,
  request URL preparation, and test environment setup. A test asserting the final
  exception type does not alone establish which code path produced it.

The E and F outcomes are natural missing-context controls. No evidence was
removed to manufacture an incomplete case, and no synthetic control was needed.
Required external source was not fetched beyond the frozen trial boundary.

## Aggregate interpretation

Each guidance condition produced one complete assessment and two incomplete
assessments. All six returned zero findings. All six strict result shapes passed
the trusted validator. Because no findings were returned, this is not a positive
exercise of finding-citation validation.

Parent checks matched the reviewers' evidence-unit hashes and declared complete
read ranges to the packets. Reading every supplied record did not make missing
dependencies available. The incomplete outcomes therefore remain substantive
coverage limits rather than collection or model-execution failures.

Self-recorded inspection time totaled approximately 650 seconds for baseline
and 674 seconds for candidate. These totals exclude some dispatch and artifact
overhead, use differing timestamp precision, and include recovered local
reading/formatting issues. They are not a controlled speed or cost benchmark.

The comparison does not establish higher detection quality, reduced false
positives, or calibrated precision/recall. It shows matching outcomes on this
small sample and preservation of the incomplete-evidence boundary. A lack of
warnings is not evidence of excellent recall when substantive cases remain held.
The conditional E concern also shows that an added diagnostic checklist can
notice the relevant path without changing the reporting decision.

The frozen adoption rule required a useful gain without weaker evidence discipline
or more speculative warnings. That rule was not met. Retaining baseline is not
proof that the candidate is worse; it avoids adopting an unproven improvement.

## Verification and operational boundaries

The installed bundle and frozen guidance hashes were rechecked unchanged. The
pre-existing local repository documents also matched their saved hashes. No
raw target source or private transcript is included in this repository document.

Live GitHub collection prepared three immutable evidence packets. The experiment
then operated offline. Its two judgments per packet were not finalized into one
official live workflow attempt. Collection-only claims were closed with
`operator_cancelled` and read back as `failed`; that bookkeeping status is not a
model-quality failure. Offline assessment outcomes are recorded separately.

Visible reviewer tool traces show local data reads and private output writes.
The trace format abbreviates long arguments, so this is not proof of OS isolation
or a complete syscall audit. No target code, tests, or packages were executed.
No GitHub comments, reviews, approvals, merges, or automation activation occurred.
No implementation test suite was rerun because the implementation was unchanged;
verification here concerns actual model assessments and their saved evidence.

## Next justified work

A new guidance trial needs dependency-complete, independently adjudicated positive
cases to distinguish improved detection from matching incomplete outcomes. The
current holds should remain preserved rather than relabeled as clean reviews or
silently enriched after scoring. Any external dependency collection requires an
explicit source/version boundary.

The layout comparison can resume only after fresh consent for the blocked local
step. It remains separate from guidance quality. See the
[reference study](SKILL_FIRST_REVIEW_REFERENCES.md) for the proposed report shape
and the [earlier archived trial](SKILL_FIRST_QUALITY_TRIAL.md) for the motivating
misses. Neither the candidate rubric nor the renderer draft is approved for
installation by this comparison.

## Sources

[1] https://github.com/encode/httpx/pull/3778
[2] https://github.com/encode/httpx/pull/3775
[3] https://github.com/psf/requests/pull/7471
