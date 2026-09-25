# Collection compatibility verification

## Observed live failure

The installed `ad8e565234fb41cb9bc89aca1e6cb8e4138cae38` bundle could not review
NousResearch/hermes-agent#118824. Attempt `b4edbd1d4b494e579dc2abc156a08f7e`
recorded `incomplete` with `docs_budget;invalid_tree`.

The captured head was `efdd27a84572cd92599eadf8bc9d01da332679aa`; base and merge
base were `ee8a919fd2769166d45ebf67f45ff5b1acec69fe`.
Live tree inspection found a legal bracket-containing filename outside the
changed paths. The tree was not truncated and contained no duplicate paths.
The collector incorrectly applied glob-disallowing selector validation to every
literal tree entry.

The selected base documents total 100895 UTF-8 bytes: AGENTS.md 31874,
CONTRIBUTING.md 51333, and README.md 17688. The 60000-byte default could not include
all three. The incomplete attempt remains preserved, not relabeled successful.

## Repairs and boundaries

Literal tree and compare paths now permit brackets and asterisks without glob
expansion. Exact path keys, percent-encoded GET requests, blob identities, and
mode checks remain required. Malformed and duplicate paths and truncated trees
still block collection. Explicit source and extra-document selectors retain
their conservative glob-disallowing syntax; only ignorePatterns performs glob
matching.

The prepare CLI exposes `--max-doc-bytes` with an inclusive range of 1 through
1000000 and the unchanged 60000-byte default. The operator may select a larger
bounded packet only when the reviewer can read it completely. Repository policy
and PR prose cannot set this limit. Required documents are never truncated.
Other collection limits are unchanged.

## Offline verification

The CLI regression first reproduced a retained `docs_budget` hold and failed
because `--max-doc-bytes 200000` was unavailable. After implementation, the
subprocess fixture collected every complete document and finalized a fixture
judgment. Repetition deduplicated; a subsequent narrower collection remained
incomplete. Invalid bounds are rejected before state creation or GitHub calls.
UTF-8 boundary tests distinguish byte count from character count.

Collector regressions exercise unrelated literal filenames, changed and renamed
paths, ancestor guidance, exact URL encoding, and rejection controls for unsafe
paths and inconsistent evidence. These are synthetic fixtures, not reviews of
live PR code.

Parent verification against the CI-pinned Hermes source passed on Python 3.11
and 3.12: 658 tests and 82 subtests on each. Ruff 0.15.10 and `git diff --check`
passed. Commands are in [the development workflow](WORKFLOW.md).

## Independent review caught a downstream size limit

The first independent review found that individually valid document and source
budgets can exceed the artifact reader's 4000000-byte ceiling after JSON
escaping. A Unicode sizing probe produced 4200390 bytes before full metadata.
That review blocked installation: a packet must not be marked `prepared` when
finalization cannot read it. Admission must check the complete serialized input
and rendered context, not just the raw document byte count.

A separate fix context added that admission check without raising the reader
limit. A real CLI fixture first returned an unreadable prepared input of 4202969
bytes and failed finalization with `result_or_input_invalid`. It now retains an
explicit `incomplete/artifact_budget` outcome with a private immutable diagnostic
only. A near-limit Unicode control still prepares and finalizes. A separate
regression covers context-only overflow caused by numbered source lines.
Parent full-suite verification after this repair passed on both Python 3.11 and
3.12: 661 tests and 82 subtests on each. Ruff and whitespace checks passed.

## Live source-helper checkpoint

With `--max-doc-bytes 200000`, the source helper prepared the same PR at the same
head and base, retaining all three base documents and all four changed-file
source records. There were no incomplete reasons. Attempt:
`212a560756424f8d800af1912a60dac0`.

This proves repaired live collection only. Independent approval, hosted CI,
pinned installation, and installed review judgment are separate gates. No PR
code was executed, no GitHub review was published, and no unattended worker or
legacy cutover was enabled.
