# Review guidance and presentation references

> Historical research and proposals. The later
> [guidance comparison](SKILL_FIRST_GUIDANCE_COMPARISON.md) retained the baseline
> rubric. A separate renderer revision was adopted after this study. See
> [current readiness](SKILL_FIRST_READINESS.md) for decisions and their limits.

## Recommendation

Borrow the concise change summary and actionable finding structure visible in
Greptile reviews, and ClawSweeper's separation of evidence, risks, and next steps.
Keep the Hermes workflow narrower: supervised static assessment, pinned evidence,
no numeric confidence, and no merge-readiness certification.

This is a reference study and candidate design, not a performance comparison.
No candidate rubric has been installed or evaluated by this work. Attractive
formatting is not evidence that a reviewer catches more bugs.

## What the supplied examples actually contain

All four supplied OpenClaw PRs were inspected through paginated GitHub issue
comments, PR reviews, and inline review comments. Counts matched the corresponding
PR metadata. Each currently has mentions of `@greptileai`, but no retrieved review
artifact authored by a Greptile account. The substantive reports are authored by
`clawsweeper[bot]`:

- **#97918:** 5 issue comments, no PR reviews or inline comments. The main report
  recommends an external plugin rather than core integration, then places security
  concerns and source-linked reasoning in collapsed details.[1]
- **#80255:** 10 issue comments, no PR reviews or inline comments. The report
  distinguishes a shared-queue design tradeoff from a blocking defect and gives
  maintainers concrete options. It explicitly says a live reproduction was not
  run by that reviewer.[2]
- **#77328:** 7 issue comments, no PR reviews or inline comments. The report leads
  with a short summary and next step, then supplies source-linked checks and a
  limitation about the lack of an independent live gateway session.[3]
- **#77216:** 13 issue comments, no PR reviews or inline comments. The report
  separates scope concerns and security concerns, identifies affected paths, and
  proposes a narrower follow-up rather than burying the requested action.[4]

These are observations about the retrieved comments, not independent validation
of their technical findings. They do not establish that Greptile never commented
historically. Mutable comments may have been edited or removed.

ClawSweeper's current source was pinned to
`efd9be863116673997c5935ba4c06321a3f122c8`. Its current documented layout differs
from the older examples: change explanation, readiness, scores, verification,
optional system context, decisions, and remaining actions, with agent-oriented
details collapsed.[6] The older comments must not be presented as demonstrations
of that exact current renderer.

For actual Greptile output, two additional public PRs were inspected:

- **NVIDIA/daqiri #122:** a summary by `greptile-apps[bot]`, an inline finding,
  and a bot review record. The visible format uses a change summary, confidence
  score, important-files overview, reviewed-commit link, and a concise finding
  with a title, explanation, and alternative fixes. The summary records that the
  configuration concern was resolved by a later commit.[8]
- **ai-code-review-evaluation/sentry-greptile #3:** a bot-authored summary review
  and six inline comments. This is an evaluation repository, not a representative
  production sample. Its output includes component-level summaries, confidence,
  and both logic and style comments.[9]

The NVIDIA PR is also a useful reminder that presentation is configurable: its
stated purpose is to reduce review noise, suppress diagrams, remove style
comments, and favor summary-only output.[8] Neither example establishes typical
Greptile accuracy, and neither bot's findings are ground truth for our evaluation.

## Principles worth adopting

### Explain the change before the verdict

Greptile's summary and ClawSweeper's change-summary guidance orient the reader
before detailed criticism.[8][6] Our report should explain the changed behavior
in one or two sentences, not repeat the PR title or enumerate every file.

### Give each finding a causal explanation

The NVIDIA inline comment connects a specific configuration combination to its
claimed consequence and offers concrete alternatives.[8] ClawSweeper's prompt
explicitly requires an introduced trigger, a causal connection, a useful line
range, and a brief actionable explanation. It rejects speculative comments and
style padding.[10]

For Hermes, the proposed writing pattern is:

**Condition → introduced change → observable consequence → narrow remedy.**

A finding still needs the existing exact patch quote and sufficient pinned
source. Fluent prose cannot compensate for an unsupported causal link.

### Keep defects, tradeoffs, and missing evidence distinct

ClawSweeper separates patch defects from unresolved compatibility or operator
risks, and #80255 illustrates an explicit maintainer choice rather than treating
all concerns as bugs.[10][2]

Our static review should not inflate a design preference into a regression.
Missing essential context remains an incomplete assessment, not a defect charged
to the author. Optional questions must not become a second route for speculative
findings.

### Make the evidence basis visible

The supplied reports link specific source locations and disclose limits on
runtime verification.[2][3] Hermes should show captured head and base identities,
static coverage, missing dependencies, and whether referenced test results came
from external CI or contributor claims. "Source-supported failure scenario" is
clearer than calling something reproduced when no reproduction ran.

### Put action near the top and details below it

ClawSweeper's documentation calls for explicit next-step intent and collapsed
agent details, rather than inferring action from ambiguous prose.[6] Hermes can
use the same communication principle without adopting that system's automation.
A reader should quickly find the finding, its consequence, and the next useful
check. Full evidence inventories belong in the local artifact, not the opening.

## What not to adopt

- **Numeric confidence or themed ranks.** These appear in the references, but our
  existing rubric excludes numeric confidence. A number is not a calibrated
  probability merely because a bot emits it.[8][9][6]
- **"Safe to merge," "patch is correct," or broad security clearance.** The
  examples use stronger verdict language than our bounded assessment supports.[8][3][10]
- **Mandatory diagrams, long file inventories, generic praise, or repeated
  sections.** Include context only when it helps explain a concrete finding.
- **Automatic closing, repair, or merging.** Those are separate ClawSweeper
  capabilities, not authority conveyed by studying its reports.[7][6]
- **Every warning from another bot.** Historical comments supply hypotheses for
  independent assessment, not labels to copy into a reference answer.
- **The entire ClawSweeper prompt.** Its policy includes project-specific routing,
  ownership, repair, and publication responsibilities beyond our manual scope.[10]

## Candidate Hermes report shape

The following is a layout proposal, not an executed review or a new result schema:

```text
Static review: [assessment status and findings outcome]
Reviewed head: [captured head] | Base: [captured base]

What changed
[One or two sentences about behavior.]

Findings
[Severity] [Specific title] — [path and narrow line range]
[Trigger, introduced behavior, consequence, and narrow remedy.]
[Exact patch quote / evidence reference.]

Coverage and limitations
[What was assessed. Essential gaps. No target code executed.]

Next useful step
[Address a supported finding or collect a named missing dependency.]
```

An empty finding list should say no supported introduced regression was found
within the stated scope. It must not imply an all-clear. Severity and workflow
status remain separate. The renderer can omit repetitive or inapplicable prose,
but not the evidence limitations.

## Candidate rubric changes

The current [review rubric](../skills/hermes-pr-review/references/review-rubric.md)
already requires introduced regressions, specific failure scenarios, exact
quotes, and honest incomplete outcomes. The gap is not a missing generic
checklist. The [archived quality trial](SKILL_FIRST_QUALITY_TRIAL.md) justifies a
small diagnostic addition:

- **Error-path pass:** trace newly introduced calls through failure handling and
  cleanup. Check relevant exception contracts for the supported runtime versions.
  Do not invent exception behavior when the contract is unavailable.
- **Fixture-validity pass:** inspect the actual inputs constructed by a new test.
  Check whether those inputs satisfy the format or API contract and whether the
  assertions distinguish a working fix from an invalid fixture or a no-op.
- **Counterexample pass:** challenge each proposed finding against guards,
  defaults, callers, and supplied dependencies. Remove disproved claims. Mark
  essential unavailable context incomplete.
- **Communication pass:** express retained findings with the causal pattern above.
  Do not add warnings to fill the template.

These are proposed additions, not changes to the installed bundle. Existing
source budgets, result validation, finding limits, and publication boundaries
remain unchanged.

## Separate presentation testing from reviewer testing

Two comparisons answer different questions:

1. **Presentation comparison:** render the same already-adjudicated judgment in
   the current and proposed layouts. Blind the format labels and assess whether
   the reader can identify the problem, evidence limit, and next step. Factual
   fidelity is a hard gate. This can show clearer writing, not better detection.
2. **Rubric comparison:** freeze current and candidate guidance before selecting
   and reviewing held-out packets. Give independent sessions the same evidence,
   model, budgets, and safety boundaries. Include quiet controls and missing-context
   cases. Keep bot comments and expected outcomes out of reviewer inputs. Then
   adjudicate supported findings, misses, unsupported warnings, incomplete outcomes,
   time, and operator burden separately.

The supplied PRs and the additional Greptile samples are now development examples,
not unseen tests for this session. A future fresh reviewer may be blinded, but
that does not undo example-driven selection by the experiment designer.

The adoption decision should require a useful quality gain without an observed
increase in speculative warnings or weaker evidence discipline. A small trial
can justify further supervised use; it cannot establish population accuracy.
No live Greptile or ClawSweeper execution comparison was performed here.

## Inspection and verification boundary

GitHub access was read-only. The study retrieved comments, review records, and
selected pinned repository documents. The full local-review skill was read as
source material; setup commands were not followed. The review-item prompt was
searched and relevant sections were read, not treated as a fully audited system.
No target checkout, target-code execution, install, GitHub mutation, schedule
change, or active reviewer-bundle edit was performed.

The four supplied PR collections have complete, deduplicated endpoint inventories.
The private evidence archive retains JSON responses, comment URLs and IDs,
pinned source files, and the citation ledger. This document records research
observations and proposed changes only.

## Sources

[1] https://github.com/openclaw/openclaw/pull/97918
[2] https://github.com/openclaw/openclaw/pull/80255
[3] https://github.com/openclaw/openclaw/pull/77328
[4] https://github.com/openclaw/openclaw/pull/77216
[6] https://github.com/openclaw/clawsweeper/blob/efd9be863116673997c5935ba4c06321a3f122c8/docs/pr-review-comments.md
[7] https://github.com/openclaw/clawsweeper/blob/efd9be863116673997c5935ba4c06321a3f122c8/.agents/skills/local-clawsweeper-review/SKILL.md
[8] https://github.com/NVIDIA/daqiri/pull/122
[9] https://github.com/ai-code-review-evaluation/sentry-greptile/pull/3
[10] https://github.com/openclaw/clawsweeper/blob/efd9be863116673997c5935ba4c06321a3f122c8/prompts/review-item.md
