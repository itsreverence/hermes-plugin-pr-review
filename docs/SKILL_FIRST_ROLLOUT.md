# Skill-first rollout gates

This record separates development, live evidence, and activation. The manual
candidate is not a replacement for the active webhook until the cutover gate
passes. No gate authorizes GitHub comments, approvals, merges, or PR-code execution.

## Acceptance gates

These are separate gates, not an automatic progression. Supervised manual use
does not depend on finishing the later polling or unattended-activation gates.
See [current readiness](SKILL_FIRST_READINESS.md) for exact-revision evidence.

1. **Useful manual evidence:** collect bounded changed-file source at immutable
   head and merge-base commits, plus explicit dependencies and relevant base
   guidance. Test missing, oversized, unsafe, and stale evidence.
2. **Real manual judgments:** review a quiet control and a real positive-finding
   case. Independently adjudicate findings from the collected evidence. Exercise
   a fresh isolated session, deduplication, and incomplete outcomes.
3. **Reviewed candidate:** pass focused and full tests, independent review, and
   hosted CI on the published branch. Preserve the legacy implementation.
4. **Deterministic polling:** paginate an explicit allowlist, retain pending work
   across failures, bound retries, fence concurrent workers, and make no model
   call when no eligible work is due. Exercise shadow mode first.
5. **Unattended boundary:** prove that the model has no tool execution, credential
   read, or GitHub-write interface. The trusted host needs provider authentication;
   tool-less inference does not establish OS isolation from same-user files.
   Prove exclusive inference ownership, a remaining-lease budget, and binding from
   each queue completion to its verified shared attempt. Shared deterministic
   helpers retain collection, state transitions, and finalization. Prompt wording
   and a fresh default session are not sandboxes.
6. **Controlled activation:** install the reviewed bundle, use only explicitly
   enrolled repositories, and verify a bounded scheduled run. Keep reports local.
   Retire the old hook, receiver, and only their public route after replacement
   proof. Preserve private rollback artifacts and exact scheduler state.

## Historical operational exception

At the recorded legacy checkpoint, the watchdog was paused because it treated an
empty GitHub hook delivery list as a recurring failure. Pausing alerts did not
validate end-to-end delivery or retire the receiver. The receiver and hook were
left intact. This is historical evidence, not a current service-health check.

## Rollback

Before activation, stop invoking the candidate. Its private state remains useful
for inspection. After installation, remove or restore only the candidate bundle
and pause its designated schedule. Never reset the legacy reviewer state, delete
review evidence, or reset device-wide Tailscale routing as part of rollback.

Record live results and any held gates in the verification record. Local fixture
success is not model-quality evidence or scheduled-production proof.
