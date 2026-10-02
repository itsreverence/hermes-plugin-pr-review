# Static-review presentation comparison

> Historical experiment record for the frozen layout draft. A later, separately
> verified renderer revision clarified completion and quiet-result wording in
> commit `eab3b8233be256ad53dbebfb08b7253895521dcc`. The preferences below do not
> measure that later revision. See [current readiness](SKILL_FIRST_READINESS.md).

## Decision

Keep the installed renderer unchanged. The candidate's headings and ordering
merit further development, but the exact draft is not ready for installation.
Both model readers preferred it for reports containing findings and tied on the
quiet report. Both identified an empty Findings section in the quiet candidate
and ambiguous completion wording shared by both layouts.

This is evidence about model-reader preference on fixed report content. It is
not evidence of better defect detection, faster human reading, or merge readiness.

## What was compared

The presentation study proposed in the
[reference study](SKILL_FIRST_REVIEW_REFERENCES.md) rendered three judgments
from the [archived quality trial](SKILL_FIRST_QUALITY_TRIAL.md):

- A: a report containing a worktree-fixture finding.
- B: a quiet static assessment of a reasoning-effort change.
- C: a report containing a DNS-error fallback finding.

The installed renderer supplied the baseline. An isolated trial-only renderer
reordered the same content under Assessment summary, Findings, Coverage and
limitations, and Reviewed evidence identity. Findings gained explicit Why it
matters and Suggested correction / next step labels. Technical prose, patch
quotes, source identities, and scope caveats stayed fixed.

The experiment did not rewrite or supplement the archived judgments. In
particular, the archived A report is not an exhaustive account of the issues
later adjudicated in that packet. Matching its wording establishes preservation,
not completeness or correctness of every shared assertion.

Two fresh model readers each received three P/Q pairs. Labels were mapped before
evaluation, and display order was reversed between readers for every case. Each
reader considered change intent, the finding or quiet outcome, causal consequence,
evidence limitations, and the next useful step. Neither received the mapping,
raw judgments, underlying code, or the other reader's result.

## Recorded outcomes

- A: both readers preferred the candidate.
- B: both readers reported a tie.
- C: both readers preferred the candidate.

These are six pair judgments on three cases, not six independent test cases.
Four favored the candidate and two tied. Neither reader identified a pairwise
change in technical meaning.

The useful differences were concrete: the assessment appeared before the long
identity block, proposed corrections had an explicit label, and the substantive
static-only restrictions appeared together under Coverage and limitations.

The weaknesses were also consistent:

- **Ambiguous status:** both layouts begin with `Outcome: completed`, without
  naming the activity that completed on that line. Later caveats limit the claim,
  but readers could mistake the banner for successful verification.
- **Empty quiet outcome:** the candidate renders a Findings heading with no body
  when there are no findings. The summary supplies the qualified quiet outcome,
  but the empty section can look unfinished.
- **Missing change intent:** headings do not supply an absent explanation of why
  the change exists. The finding-heavy summaries describe concerns more clearly
  than they describe the intended change.
- **Dense paragraphs:** labelling the consequence and correction improves
  navigation, but does not simplify a long causal chain or prioritize follow-up.

## What the evidence supports changing next

A later renderer candidate can name the scope directly in its status line, such
as `Static assessment: complete`, while retaining the durable outcome separately.
It should explicitly state the qualified quiet result rather than leave Findings
empty. An incomplete result must remain incomplete, even when it returns no
findings. A status-label change must not relabel a failed or prepared attempt as
a completed assessment.

Keep the stronger headings and nearby evidence. Do not invent a change-intent
statement or a corrective action when the judgment supplies neither. Any revision
would be a new artifact requiring its own validation; the reader preferences
above apply only to the frozen draft, not to an improved version that did not run.

## Verification and boundaries

The authorized local generation produced all three paired layouts. The trusted
result validator accepted all three archived judgments and their patch citations.
Every declared technical factual unit appeared byte-identically in both layouts.
The parent also verified all 109 quotations in the two reader reports against
their assigned layouts, decoded the preferences using the frozen mapping, and
checked the resulting totals programmatically.

Private evidence includes `layout-fidelity.json`, `layout-map.json`, both
`layout-reader-*.json` files, `layout-adjudication.json`, and the rendered pairs.
Packet, guidance, and installed-bundle consistency checks passed. The study did
not execute target code or tests, finalize a live review, publish on GitHub, or
install a renderer.

The earlier approval-blocked attempts remain preserved. Fresh authorization
allowed this local generation step to run. Those interruptions are operational
history, not failed model preferences or a reason to discard completed results.

Both readers used the session-reported same model family. The reports are a
small, selected development sample. No human usability study, measured reading
speed, independent technical truth evaluation, or live Greptile/Clawsweeper
comparison took place. The separate second-pass detection trial must be scored
on its own evidence.
