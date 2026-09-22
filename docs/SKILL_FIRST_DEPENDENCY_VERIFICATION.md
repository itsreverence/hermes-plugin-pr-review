# Explicit dependency collection verification

## Observed evidence gap

A private static trial of NousResearch/hermes-agent#118840 stopped incomplete.
The changed-source packet lacked the HTTP safety client, source adapters, and
worker implementation needed to assess the refactor. A second attempt requested
`tools/url_safety.py` explicitly and stopped with `sources_budget`.
Neither attempt was a clean review. Both remain preserved.

The captured head is `d2e7e95b213a3610319fe9af920e34e28648f14d`.
The base and merge base are `ee8a919fd2769166d45ebf67f45ff5b1acec69fe`.
The original packet contains 343511 UTF-8 source bytes. Authenticated trees at
both source pins identify these required repository paths:

- `tools/url_safety.py`: 30949 bytes per side.
- `tools/skills_hub_official.py`: 19211 bytes per side.
- `tools/skills_hub_skillssh.py`: 18594 bytes per side.
- `tools/skills_hub_sources.py`: 20944 bytes per side.
- `tools/skills_hub_clawhub.py`: 28070 bytes per side.
- `tools/daemon_pool.py`: 3091 bytes per side.

Together, those dependency records require 241718 bytes. Each path has the same
blob on both sides, but both full records still count toward admission. These
sizes justify a 250000-byte dependency allowance for this bounded retry, not a
larger default or recursive dependency crawl. The earlier 137221-byte document
packet also requires the existing explicit 200000-byte document allowance.

## Collection contract

The new prepare-only `--max-dependency-bytes` option reserves space only for
explicit paths outside the change set. Its inclusive range is 1 through 400000.
The flag requires review stage and at least one `--source-path`.

Without the option, sources retain the shared 400000-byte ceiling. With it,
changed-file paths retain that independent ceiling. Renames, ignored paths,
explicit overlap, and repeated requests cannot move changed source into the
new allowance. Both pinned sides, original source text, exact blob checks, and
required omissions remain enforced.

Explicit dependency ancestors also select guidance at the trusted base. The
existing document, request, deadline, path-count, and serialized-artifact limits
remain in force. `source_budget` records admission accounting in both evidence
representations. `dependency_sources_budget` identifies a dependency-only hold.

See [the manual procedure](SKILL_FIRST.md#retry-a-required-dependency-hold).

## Verification boundary

The CLI regression-first run returned 14 failures and one passing negative
control before the flag existed. The intended failures were rejection of the
new flag and inability to collect the complete dependency evidence.

Tests of this repository exercise the reviewer, not the target PR. Real review
trials use authenticated GET-only collection and static judgment. No target PR
code, tests, or external plugin implementation may run. No trial result may be
posted to GitHub. Installation, hosted checks, completed judgment, and repeated
result reuse are separate gates, not implied by a successful collector test.

## Offline verification

Collector regressions first returned 55 failures and one passing control.
The implemented collector passes all 56 new cases. CLI integration passes all
17 cases, including byte boundaries, immutable earlier holds, completed-result
reuse, GET-only calls, and independent serialized-artifact overflow rejection.

The complete legacy and skill-first suites pass on Python 3.11 and 3.12:
734 tests and 82 subtests on each. Tests use the CI-pinned Hermes source and
SDK versions documented in [the development workflow](WORKFLOW.md).
Ruff 0.15.10 and `git diff --check` pass. These are offline tests, not target-PR
runtime verification or proof of unattended safety.
