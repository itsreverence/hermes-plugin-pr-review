# Clearer static-assessment reports

## Candidate scope

This presentation-only revision changes the repository's manual skill renderer.
It does not install that renderer into an active Hermes profile or change the
legacy plugin renderer. Findings, review instructions, result schema, ledger
transitions, collection, and publication authority stay unchanged.

The revision addresses the ambiguous completion banner and empty quiet section
identified in the earlier presentation study. It does not adopt a second review
pass or claim improved defect detection.

## What changes in a report

The header names the activity and coverage explicitly:

```text
Static review assessment: complete
Workflow outcome: completed
Not a merge approval.
```

The workflow outcome remains the existing durable-state label. It is not renamed
in JSON or SQLite. The renderer accepts only the matching final pairs
`completed`/`complete` and `incomplete`/`incomplete`, so a failed, prepared, stale,
or inconsistent record cannot be presented as a completed assessment.

Reports put Assessment summary before Findings. Each finding retains its exact
prose, patch evidence, severity, source path, diff path, side, line, and commit,
with explicit Why it matters and Suggested correction / next step labels.
Coverage and limitations follows, then Reviewed evidence identity. The renderer
does not invent change intent or rewrite a finding.

A complete review with no findings says:

> No concrete introduced defects were reported in the supplied evidence.

An incomplete review with no findings says:

> No findings established. Assessment incomplete; missing context prevents a complete review.

Both retain the static-only, no-execution, no-tests, no-publication, and
not-a-merge-approval caveats. Incomplete reports retain every supplied limitation,
including when they also have useful findings.

Triage has a separate Static triage assessment label and Triage decision section.
Its scope is PR metadata and changed-file statistics, with Implementation not
reviewed stated explicitly. The supplied decision, reason, and routing confidence
are displayed without a Findings section or an implementation-review claim.

## Exercised evidence

The baseline full suite passed 734 tests and 82 subtests on Python 3.11 before
the change. New presentation regressions then produced 16 expected failures and
four passes against the old renderer. The failures covered scope labels, quiet
outcomes, triage scope, headings, and rejection of nonfinal or mismatched status.

After implementation:

- Focused renderer and prepare/finalize checks: 40 passed.
- Full Python 3.11 suite: 754 tests and 82 subtests passed.
- Full Python 3.12 suite: 754 tests and 82 subtests passed.
- Ruff 0.15.10 and Git whitespace checks passed.

The focused suite covers nonmutation and determinism, complete and incomplete
reviews, incomplete reviews with findings, triage, renamed LEFT-side citations,
and the real local prepare/finalize seam with a fake read-only collector.
This verifies the trusted reviewer's behavior, not execution of any target PR.

An offline replay rendered positive, quiet, incomplete, and triage examples
through both the installed baseline and repository candidate. The first three
reuse archived static judgments; triage is explicitly synthetic. All four paired
record JSON files are byte-identical. Every checked summary, limitation, finding
prose, citation, and identity unit remains in the output. Triage adds its supplied
routing confidence rather than synthesizing a new one.

Replay envelopes derive workflow status for presentation and are not finalized
live reviews. No fresh model judgment, GitHub collection, target execution,
publication, or installation is established by those examples.

## Compatibility and deployment boundary

Markdown headings and labels change; the structured result remains the source
for machine consumers. Existing persisted reports are not rewritten. Changing
`workflow.py` changes the bundle digest under the existing implementation, so a
later installation must follow normal bundle verification and fresh-attempt
rules. This revision does not bypass a digest mismatch for an in-flight review.

The installed reviewer, previous trial evidence, and unrelated working-tree
changes are preserved. A local verified candidate is not a deployed release or
hosted CI result. Installation and GitHub publication remain separate decisions.
