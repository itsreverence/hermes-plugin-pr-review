# Focused second-pass review trial

> Historical experiment record. Its no-advancement decision remains in force.
> The proposed follow-up evidence comparison is now closed as
> infrastructure-incomplete and unscored. A separate informed investigation did
> not repair that comparison or justify a second pass. See
> [current readiness](SKILL_FIRST_READINESS.md).

## Decision

Retain the installed single-pass workflow. The tested second pass does not meet
this experiment's advancement gate, so this milestone does not advance to
private dogfood, installation, or unattended operation.

The second pass retains all four supported first-pass findings and improves two
explanations. Its only added warning concerns a real client-ownership mismatch,
but the claimed failure still depends on missing HTTPX lifecycle evidence. A
plausible concern is not an independently supported detection gain.

The informed independent closeout audit agrees: no advancement. Its saved
`decision-audit.json` was read back and hash-verified, and both trusted artifact
verifiers passed. The audit is separate adjudication, not a third experimental
pass or independent human ground truth.

## The comparison tests marginal value

Four frozen packets represent two positive cases, a quiet control, and a
missing-context control. Each receives one fresh first pass and one fresh second
pass using the same reported model, `gpt-6-astra` through `openai-codex`.
The second reviewer receives the complete packet and frozen first judgment,
not the reference answers or other reviewers' outputs. It must return a complete
replacement judgment and an explicit change ledger.

The intervention therefore includes extra compute and first-pass context. It is
not two independent arms or an equal-compute comparison. No prompt tuning or
performance retry follows the observed outcomes.

The baseline rubric, protocols, packet inventory, and advancement gate were
frozen before experimental first- and second-pass inference. Some reference
work began before the final experiment-freeze receipt. Reference artifacts were locked before the
first pass. The newly proposed H2 reference finding was separately challenged
while first-pass reviews ran, and its disputed impact was recorded before
second-pass dispatch. Original reference artifacts were not rewritten.

The frozen gate requires at least one added, independently supported reference
issue, no loss of supported findings, no added unsupported warning, preserved
missing-context status, and bounded time overhead. The parent applies the time
ceiling conservatively to first-plus-second assessment time, at most 2.5 times
first-pass time.

## Results by case

- **G, scratch cleanup and regression fixture:** three findings before and after.
  Both passes identify an uncaught symlink-loop exception, incorrect rebasing of
  an already-relative gitdir, and a literal backslash-n in the fixture. The second
  pass refines cleanup consequences and replaces an overly broad cwd condition
  with a concrete POSIX example. No issue is added or removed.
- **H, resolver-scoped HTTP pooling:** one finding becomes two returned warnings.
  Both retain the local DNS-exception/catch mismatch in stale-index fallback. The second
  adds the disputed lifetime warning described below. That addition is not
  counted as a confirmed gain.
- **I, quiet reasoning-effort control:** complete bounded static assessment,
  zero findings in both passes. This is not live provider or merge verification.
- **J, missing exception-propagation context:** incomplete with zero findings in
  both passes. Missing dependency behavior and test-path context stay explicit.

The initial primary reference set contained G1, G2, H1, and H2. H2's broader
failure claim remains uncertain after challenge. The first pass therefore
recovers all three confirmed primary issues plus the separately tracked G3
fixture defect. The second retains those same four supported findings. This
accounting preserves the original reference count rather than silently changing
its denominator or calling an uncertain omission a confirmed miss.

The audit narrows H1 to the supplied exception producer and catch-list mismatch.
The packet does not establish every HTTPX version's propagation or the old DNS
mapping end to end. That caveat applies equally to both passes. G3 is a malformed
test fixture, not another demonstrated production failure or proof the test fails.

## The added lifetime warning does not close its causal gap

The packet demonstrates that short-name search runs inside the new HTTP session.
Its executor explicitly copies ContextVars into workers. Search can stop waiting
without stopping already-running workers, so a worker can retain the client
binding after the owning context exits. This is a supported ownership mismatch,
not a disproven concern.

The second pass adds a more specific BrowseSh cache scenario than the original
reference challenge considered. BrowseSh participates in initial fan-out, not
the eight-second registry fallback discussed in the earlier ClawHub hypothesis.
In the supplied source, `_get_json` returns
`None` for an HTTP error, `_fetch_catalog` converts that to an empty list, and
`_memo_json` accepts that list for disk caching with the normal one-hour TTL.
If that conditional write occurs, it can affect later invocations, not only
workers in the current process. Those local downstream steps are visible.
However, the claimed trigger is that exiting the shared client context
interrupts an in-flight read with an HTTP error. The applicable HTTPX and
HTTPcore lifecycle behavior is absent. A later GET failing after context exit
also relies on the missing client contract.

The empty-on-error cache behavior already exists on the base side. Establishing
an introduced cache regression requires the missing new trigger, not merely
rediscovering that old behavior. The warning is therefore insufficiently
supported as written, not established false. It cannot satisfy the no-unsupported-
warning gate. The second H judgment also says `complete` with no limitations;
that status does not acknowledge its added finding's causal dependency gap.

The original judgments remain unchanged. This adjudication does not fetch more
evidence, repair the experimental output, or rerun the reviewer to obtain a
preferred result.

## Time and verification

Summed worker-recorded assessment intervals are:

- First pass: 980.000 seconds, approximately 16.33 worker-minutes.
- Additional second pass: 1,413.143 seconds, approximately 23.55 worker-minutes.
- Combined: 2,393.143 seconds, approximately 39.89 worker-minutes, or 2.442 times
  first-pass time. This passes the conservative 2.5-times time ceiling.

The contract does not state unambiguously whether its time ceiling applies to
the added pass or the combined workflow. The added-pass ratio is 1.442 and the
combined ratio is 2.442; both satisfy 2.5. This ambiguity therefore does not change
the gate result, but future contracts should name the interval explicitly.

The retained orchestration traces use a wider task-duration interval: 1,112.15
seconds for the four first-pass tasks and 1,505.14 seconds for the second-pass
tasks. Their combined ratio is 2.353. These are worker-time sums, not the elapsed
latency of parallel batches, token usage, or billed cost. The study does not
provide token or billing measurements. Reference work, challenges, parent
adjudication, approvals, and layout evaluation are outside these pass-time sums.

The exercised read-only verifiers confirm all eight strict judgments and patch
citations, all 20 assessment artifacts at mode 0600, and declared read coverage
for all 63 indexed unit identities in each pass. They also confirm unchanged
first-pass and reference locks, packet bytes, protocols, guidance, installed
reviewer bundle, and previously captured repository documents. These checks do
not prove semantic reading or the correctness of a causal argument.

The private trial root retains `verify_artifacts.py`,
`verify_completed_passes.py`, `completed-pass-verification.json`,
`trace-timing-verification.json`, original references, their locks, both passes,
change ledgers, the reference challenge, and `decision-audit.json`. Re-running either verifier is a
local consistency check, not a new review or execution of target code.

The inspected traces show local evidence reads and artifact writes, with no
observed target execution, network collection, publication, or finalization in
these assessment passes. Some tool arguments are shortened, so traces and hashes
are not an OS sandbox or independent proof that every possible side effect was
absent. No live ledger completion or current-head review is claimed. No target
code or target tests ran as part of the parent verification. Helper test results
from earlier milestones are separate evidence, not rerun results for this trial.

## Limits and next decision

The confirmed reference issues were already recovered by the first pass, leaving
little headroom for reference-based improvement. This selected historical sample
can expose ungrounded additions and overhead, but cannot prove that second passes
never help. References, reviewers, and audit use the same model family rather
than independent human ground truth. All defects are bounded static findings,
not reproduced production incidents.

The presentation study is separate. Its candidate headings were preferred for
the two reports with findings, but completion wording and quiet output still
need work. See [the presentation comparison](SKILL_FIRST_PRESENTATION_TRIAL.md).
No renderer change is adopted here, and layout preference does not offset the
failed detection gate.

Any future work needs a new bounded decision rather than automatic progression.
The useful choices are a presentation-only revision or a separately specified
evidence-sufficiency study. Neither authorizes an install, publication, schedule,
merge, target-code execution, or an unbounded sequence of model retries.
