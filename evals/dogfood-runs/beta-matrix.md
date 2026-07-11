# Dogfood Beta Matrix

Purpose: a compact, no-post evaluation ladder for deciding whether Hermes PR Review is worth letting speak on GitHub. Posting mechanics are proven separately in `evals/dogfood-runs/2026-07-08-posting-safety-canaries.md`; this matrix is about review quality.

Default command pattern:

```bash
hermes pr-review review OWNER/REPO#123 --json
```

Keep `--post-comment` out of matrix runs unless a row explicitly says it is a posting canary.

## Scoring rubric

For each run, classify the output as one of:

- `post_worthy`: actionable, evidence-cited, severity-appropriate, and not noisy.
- `useful_but_edit`: real signal, but wording/severity/context needs tuning before public posting.
- `artifact_only`: useful locally, not worth a GitHub comment.
- `noise`: false positive, duplicate, too vague, or misleading.
- `miss`: reviewer missed an obvious issue expected for this case.

A repo should not graduate to findings-only posting until recent runs are mostly `post_worthy` / `artifact_only` with no repeating `noise` or `miss` pattern.

## Matrix

| Order | Case | Category | Source | Expected behavior | Posting allowed? | Evidence to capture |
|---:|---|---|---|---|---|---|
| 1 | `fastapi/fastapi#15815` | small docs public control | bundled public manifest | low risk; no actionable findings | no | artifact paths, docs loaded, false-positive check |
| 2 | `facebook/react#36839` | public security-shaped fix | bundled public manifest | reason about injection boundary without noise | no | whether finding is evidence-cited and safe to post |
| 3 | `django/django#21516` | backend correctness/data integrity | bundled public manifest | collision/compatibility reasoning; no speculative noise | no | useful findings vs false positives |
| 4 | `microsoft/playwright#41396` | model-facing browser tooling | bundled public + graph promotion manifest | catch API/output behavior risks; compare baseline vs graph when available | no | baseline/graph comparison, misses/noise |
| 5 | `vitejs/vite#22733` | public CI-failing refactor | bundled public manifest | check-context aware review; valid findings should update expectations instead of being treated as noise | no | CI context, safe-to-post judgment, expectation update |
| 6 | next public repo PR | fresh active work | public workload | evaluate normal workflow usefulness | no first; canary only after scoring | manual score and follow-up patch notes |

## Completed no-post runs

### 2026-07-08 — public beta rows 1–3 — `balanced` / `baseline`

Run summary: local artifacts were inspected during the session; raw context/review artifacts are intentionally not committed.

| Matrix row | Case | Findings | Risk | Docs | Skipped | Truncated | Checks | Score bucket | Would post publicly? | Notes |
|---:|---|---:|---|---:|---:|---|---|---|---|---|
| 1 | `fastapi/fastapi#15815` | 0 | low | 1 | 0 | false | skipped:5, success:29, unknown:1 | `artifact_only` | n/a; findings-only should stay silent | Clean docs-control behavior; no false positive. Spot-check of changed Ariadne URL returned HTTP 200. |
| 2 | `facebook/react#36839` | 0 | low | 3 | 0 | false | skipped:4, success:239 | `artifact_only` | n/a; findings-only should stay silent | Correctly summarized `innerHTML` removal and `textContent` safety boundary without inventing residual XSS noise. |
| 3 | `django/django#21516` | 0 | low | 1 | 0 | false | skipped:9, success:11, unknown:25 | `artifact_only` | n/a; findings-only should stay silent | Correctly treated netstring cache-key collision fix/tests/docs as low-risk. No obvious miss from diff inspection. |

Decision: rows 1–3 are low-noise and match expectations. Continue through the remaining public rows before enabling any public posting.

### 2026-07-08 — public beta row 4 — `balanced` / `baseline`

Run summary: local artifacts were inspected during the session; raw review JSON is intentionally not committed.
Manual public-case scores are recorded in `evals/dogfood-runs/manual-scores.jsonl`.

| Matrix row | Case | Findings | Risk | Docs | Skipped | Truncated | Checks | Score bucket | Would post publicly? | Notes |
|---:|---|---:|---|---:|---:|---|---|---|---|---|
| 4 | `microsoft/playwright#41396` | 2 | medium | 4 | 0 | false | success:1 | `post_worthy` | yes, after human confirmation | Found credible compression correctness risks: global threshold collapse and hidden interactive descendants. |


Decision: row 4 is strong enough to continue to one current public PR in no-post/watch/webhook mode, but not enough for broad posting. Truncated diffs remain artifact-only.

### 2026-07-09 — public beta row 5 — `balanced` / `baseline`

Run summary: local artifacts were inspected during the session; raw context/review artifacts are intentionally not committed.
Manual score is recorded in `evals/dogfood-runs/manual-scores.jsonl`.

| Matrix row | Case | Findings | Risk | Docs | Skipped | Truncated | Checks | Score bucket | Would post publicly? | Notes |
|---:|---|---:|---|---:|---:|---|---|---|---|---|
| 5 | `vitejs/vite#22733` | 1 | medium | 3 | 0 | false | failure:1, neutral:3, skipped:2, success:11, unknown:1 | `post_worthy` | yes, after human confirmation | Found a concrete clean-URL/query module-type correctness risk; manifest expectation updated from max 0 to max 1. |

Decision: public scored record count is now 5 with 2 `post_worthy`, 3 `artifact_only`, no unsafe-to-post records, and no truncated public-post candidates. This satisfies the local report's `ready_for_repo_canary` gate, but any actual posting should still be a single-repo findings-only canary.

## Per-run worksheet

```markdown
## YYYY-MM-DD — OWNER/REPO#123 — mode/variant

- Matrix row:
- Category:
- Artifact path:
- Mode / variant:
- Findings count:
- Risk:
- Docs loaded:
- Skipped files:
- Diff truncated:
- Posted comments:
- Score bucket: post_worthy / useful_but_edit / artifact_only / noise / miss
- Useful findings:
  - ...
- False positives / noise:
  - ...
- Missed obvious issue:
  - ...
- Would post publicly? yes/no/only-after-edit
- Follow-up patch needed:
  - ...
```

## Graduation rule of thumb

Only consider repo-level findings-only posting after:

- at least five relevant no-post reviews have been manually scored;
- no repeating false-positive category is present;
- any `high` severity finding is backed by a concrete line/path and exploit/failure mode;
- generated/truncated diffs remain blocked from posting;
- `watch-run --json` dedupes reviewed heads;
- `status --json` reports the intended posting policy before every canary.
