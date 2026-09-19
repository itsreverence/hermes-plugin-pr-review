# Manual candidate verification

This note records bounded checks of the manual candidate, not deployment or
release readiness. Raw packets and judgments remain in private local state.

## Automated checks

Baseline: the unchanged plugin suite passed **187 tests** on Python 3.11 using
the installed Hermes source (`21b2095d00a98b8ad7b5c60b10587619c852cdb8`).

The candidate passed **286 tests plus 82 unittest subtests** on Python 3.11 and
3.12, against both that installed source and the CI-pinned Hermes source
`7acaff5ef2bcbaa22bd23b72efe60906123a4f55`. The pinned-source runs each used a
fresh temporary HERMES_HOME. Commands, with the corresponding source path:

```bash
HERMES_HOME=/private/test-home PYTHONPATH="$PWD:$HERMES_TEST_SRC" uv run --no-project --python 3.11 --with pytest --with pyyaml python -m pytest tests/plugins tests/skill_first -q -p no:cacheprovider
HERMES_HOME=/private/other-test-home PYTHONPATH="$PWD:$HERMES_TEST_SRC" uv run --no-project --python 3.12 --with pytest --with pyyaml python -m pytest tests/plugins tests/skill_first -q -p no:cacheprovider
uv run --no-project --python 3.11 --with 'ruff==0.15.10' ruff check --isolated --no-cache plugins tests scripts skills
git diff --check
```

The **99 candidate tests** cover real SQLite/filesystem behavior, multiprocess
claims, expiry fencing, deduplication, rerun retention, stale snapshots, rejected
citations, malformed output retention, trusted-base collection, rate-limit
budgets, and full CLI subprocesses. GitHub and judgment are fixtures in those
tests. No automated test invokes a real model or claims to review a real PR.
Ruff and in-memory compilation of all candidate Python modules passed. Static
added-line checks found no secret literals, shell injection, eval/exec,
unsafe deserialization, or formatted SQL matches.

## Live manual checks

These checks used real GitHub GETs and judgments authored by the active default
Hermes session (`gpt-6-astra`). The helper did not invoke a separate model client.
The model label is operator-reported, not independently attested. No reviewed
PR code was executed, and no GitHub write was requested.

- **FastAPI #16377:** triage collection returned `incomplete` with `docs_budget`.
  The helper retained the collected packet and did not invite a clean assessment.
- **Packaging #1409:** triage completed and recommended a focused code review.
  A repeated prepare skipped it and linked the original completed attempt.
  Head: `47b0db5b7b9a5fd9f4d1d3236e90f205c8b34625`.
  Base: `10590c194edb33c82f84a127883d6097c56b7840`.
- **Packaging #1409 review:** returned `incomplete` because the single patch hunk
  lacked the replacement-method implementation and surrounding property-test
  context. It did not turn an empty findings list into a clean result.
- **This project's #10:** an explicitly retrospective review (`--allow-closed`)
  of the CI-action-major update completed with no actionable defect identified
  in the narrow supplied workflow diff. An unchanged second prepare deduplicated.
  Head: `f500f7f196e818257879d5cf43fe607a23f4f890`.
  Base: `688026b5948f1c81c9e948d03348a1b8ba1f4b49`.
- **Intentional rerun:** `--rerun-reason` admitted a separate attempt on that same
  CI PR and preserved the earlier report. The operator then cancelled that
  admission test with `fail --reason operator_cancelled`. This proves rerun
  admission and failure recording, not a second completed model review or a real
  provider timeout.

The original bounded canary ledger contains seven attempts: two completed,
two incomplete, two deduplicated, and one deliberately cancelled/failed. All
were read back; none remained active. Completed input digests and head/base
bindings matched their reports. Managed files/directories were verified as
0600/0700. Raw artifacts are not included in the repository.

After the independent-review fixes, a fresh final-candidate canary repeated the
real CI PR review and Packaging triage, then repeated both prepares. Its four
attempts were two completed and two deduplicated, with no active attempts.
Read-back verified input digests, matching head/base identities, retained
reports, and 0600/0700 managed permissions against the final bundle digest:
`0d25eaa5d1378f066a83a3e145ea5f7035339f695490862d66ff4f2f3c4137d1`.
This digest establishes local source consistency, not independent attestation
of GitHub or model provenance.

These are exact-snapshot observations, not enduring current-head assurances.
The candidate was not installed. Existing plugins, cron jobs, service units,
webhooks, watched-repository state, and the original checkout were not changed.

## Independent review

The first read-only review found two blockers: Unicode line separators could
forge citation locations, and a title/body race could reuse stale triage.
A separate fix pass added collector-through-finalize regressions and corrected
both. Citation parsing now follows Git's LF records, preserving other separators
as content. Triage compares title/body on the final collection read before any
deduplication decision. The final automated counts above include these cases.
A fresh independent re-review reported no remaining security or logic blockers.
That reviewer ran the 99-test candidate suite and all 40 focused regression
cases, plus Ruff and whitespace checks. Its documentation-count suggestion was
resolved by rerunning the full matrix and updating this record.

## Remaining gates

This is a working manual PoC with deliberately narrow evidence coverage. Before
using it broadly, improve SHA-bound surrounding-source collection and relevant
document selection. The live results show why these are useful work, not reasons
to weaken the incomplete gate. The context renderer also uses JSON strings:
when a reader truncates long lines, decode saved strings as text rather than
assuming the omitted content was reviewed. Never execute those strings.

Real positive-finding quality, broader repositories, fresh isolated-session
handoffs, enforced execution/credential restrictions, a paginated scanner,
no-change scheduler gating, actual scheduling, installation, and production
cutover remain unproven or unimplemented. Local tests do not establish hosted
GitHub Actions CI results for this unpushed branch. The separate upstream CLI
compatibility issue is not a dependency of these standalone helpers.
