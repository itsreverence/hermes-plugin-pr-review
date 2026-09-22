# Source-enriched manual verification

This record supplements the [initial manual candidate record](SKILL_FIRST_VERIFICATION.md).
It separates real GitHub reads, model judgments, fixtures, and deployment. The
observations below were made on 2026-09-22 UTC. They do not enable scheduling.

## Fresh-context manual reviews

The collector used real authenticated GitHub GETs. A fresh default-model review
session read the prepared packets and authored strict judgments with
`gpt-6-astra`. The parent independently checked the causal findings and finalized
through the helper, including live metadata rechecks. Model identity is
session-reported, not independent attestation. Neither session executed PR code,
ran PR tests, or posted on GitHub.

The source-enriched bundle digest for these runs was
`c619c7361de7cd739fa8bd6d848b0a1edde3d1e6ba3be7165d55f95259e6c8bf`.
Later bundle changes require a separate final-candidate check.

### Positive-finding case: Playwright #41396

This was an explicitly retrospective review of the closed PR.

- Head: `3d068ea257deebb689a3e1e9bbafb3c008393cca`.
- Base and merge base: `32883517ffe7725ef45ac2dc020a63962c27d7a3`.
- Evidence: four patches, six full source records, five selected base documents.
- Outcome: completed bounded static review, two warnings.

The first warning identifies interactive descendants being discarded before the
preservation check when their repeated ancestor is collapsed. The second identifies
shared indentation/signature counters suppressing a separate short list after a
large list exhausts its allowance. Both cite collected head-side lines in
`ariaCompression.ts` (145 and 151). Parent inspection confirmed the branch order
and lack of parent identity in the counter key. These are static causal findings,
not runtime reproductions or an upstream-maintainer verdict.

### Quiet control: this project's #10

This was an explicitly retrospective review of a merged CI-action update.

- Head: `f500f7f196e818257879d5cf43fe607a23f4f890`.
- Base and merge base: `688026b5948f1c81c9e948d03348a1b8ba1f4b49`.
- Evidence: one patch, both full workflow sources, four base documents.
- Outcome: completed scoped review, no actionable defect established.

This does not audit the external action implementations or assert their CI status.

### Read-back and missing-evidence control

Both reviews finalized successfully. Repeating each prepare produced `skipped`
with the correct prior completed attempt. Read-back verified four ledger rows
(two completed, two skipped), input digests, matching commit bindings, retained
reports, no active attempts, and 0600/0700 managed permissions.

A separate real Packaging #1409 collection deliberately requested the nonexistent
operator-selected path `__operator_missing_dependency_canary__.py`. It ended
`incomplete` with `missing_source`, retained both missing-side omissions, and
never invited judgment. This is a controlled negative test, not a claim that the
PR has a missing dependency.

## Live provider boundary probe

A separately authorized synthetic triage probe reached the real `openai-codex`
route and returned a schema-valid result. Requested and provider-reported model
were `gpt-6-astra`, reasoning was high, and the request exposed no tools. The
response explicitly rejected an instruction-like fixture title as untrusted.
The terminal invocation exited 0 after approval. Earlier approval timeouts did
not execute the probe.

This demonstrates one live tool-less inference call. It does not prove an OS
sandbox, long-run provider reliability, or unattended-worker safety. The trusted
host still needs provider authentication. No external delivery was attempted.

## Independent transport review

The source collector and workflow review found no blocking defect. The separate
transport review found issues that the first green unit suite did not expose:
unbounded stream accumulation, heartbeat reads exceeding the advertised deadline,
terminal-only tool output being ignored, and commentary being mixed into the final
JSON. It also identified raw-client cleanup and future inference-ownership work.

Targeted regressions reproduced the defects before repair. The adapter now guards
decoded events before the real parser, rejects terminal-only tool output, selects
one final answer, disables SDK retries, and closes the owned client. A POSIX
main-thread deadline covers provider resolution, request setup, and streaming.
Existing alarms and non-main-thread calls fail before inference. SDK frame decoding
itself is not size-bounded by this adapter. Independent re-review passed for manual
installation and opt-in supervised helpers, with no blocking security or logic
findings. The reviewer independently exercised 14 offline probe groups using the
installed parser and SDK, including one-request-only handling of HTTP 503. The
original live probe did not establish these repaired behaviors.
Concurrent inference ownership and binding queue acknowledgments to actual shared
attempts remain gates for a future scheduled worker, not manual installation.

## Post-repair native end-to-end canary

The repaired supervised `judge` CLI completed a new real retrospective review of
this project's #10. It collected evidence, called `openai-codex` once using
`gpt-6-astra` at high reasoning, validated the response, rechecked GitHub metadata,
and finalized a completed ledger row and local report. Read-back verified the
exact result, retained route metadata, matching commits, and zero exposed tools.
The scoped result identified no actionable defect; no PR code or tests ran.

The exercised bundle digest was
`666f7705daeaef6b4d29d48d23d280809efae7a916db44e149cc0ad43e5cce9c`.
This verifies the repaired live path, not scheduling, OS sandboxing, or a
multi-run reliability benchmark.

## Automated verification

The parent ran the combined plugin and skill suite after transport repair:

- Python 3.11 with installed Hermes source: **474 passed, 82 subtests passed**.
- Python 3.12 with installed Hermes source: **474 passed, 82 subtests passed**.
- Python 3.11 with CI-pinned Hermes source: **474 passed, 82 subtests passed**.
- Ruff 0.15.10 and whitespace checks passed.

The CI-pinned Hermes commit is `7acaff5ef2bcbaa22bd23b72efe60906123a4f55`.
The parent runs included OpenAI SDK 3.17.0 and httpx 0.28.1, with no skipped
transport tests. CI declares those dependencies so a green job cannot silently
skip the real-SDK offline seam. Mock HTTP transport tests make no model, socket,
or credential calls. Hosted CI and installed-copy evidence are separate gates.

## Shadow discovery

Two real shadow scans of the explicitly enrolled repository completed in fresh
private scanner state. Both found no open eligible PRs and emitted
`wakeAgent:false`. Read-back showed applied scan 2, no candidates, no worker lease,
and private permissions. No model was invoked by the scanner.

This establishes live empty-queue discovery only. Multi-page listings, retries,
lease fencing, and durable pending work are covered by offline tests, not by
these empty live scans. A scheduled worker and legacy retirement remain gated.
