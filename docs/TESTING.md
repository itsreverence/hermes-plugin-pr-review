# Testing and legacy dogfood

The local gate covers both implementations. Dogfood, webhook, and posting
commands below belong to the legacy plugin, not the manual skill. For current
manual evidence and checks, see [readiness](SKILL_FIRST_READINESS.md) and
[the development workflow](WORKFLOW.md).

## Local gate

```bash
export HERMES_AGENT_SRC=/path/to/hermes-agent
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m pytest tests/plugins tests/skill_first -q
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m py_compile plugins/pr_review/*.py
ruff check plugins tests scripts skills
git diff --check
```

CI runs both the legacy plugin suite and the manual skill-first suite on Python 3.11 and 3.12. Only the legacy suite requires the pinned Hermes Agent API checkout. Use Ruff 0.15.10, matching CI.

For the manual candidate alone, run `python -m pytest tests/skill_first -q`. See [manual candidate scope and operation](SKILL_FIRST.md). These tests do not install the skill, call a model, or write to GitHub.

## No-post dogfood

Start with the bundled public corpus:

```bash
hermes pr-review eval-manifest --json
hermes pr-review dogfood-run --no-llm --limit 1 --json
hermes pr-review dogfood-run --case small-docs-fastapi-15815 --json
```

`--no-llm` is a fast context/artifact smoke, not review-quality evidence. Real quality evaluation requires configured model access and manual inspection.

For paired graph evaluation:

```bash
hermes pr-review dogfood-run \
  plugins/pr_review/evals/graph_promotion_prs.json \
  --variant baseline --variant graph \
  --graph-local-repo-map /path/to/graph-repos.json \
  --graph-context-binary codegraph \
  --run-id graph-promotion-001 \
  --json
```

Graph context remains opt-in until scored evidence shows it improves reviews without a useful-miss or noise pattern.

## Score results

Use these quality buckets:

- `post_worthy`
- `useful_but_edit`
- `artifact_only`
- `noise`
- `miss`

Persist scores rather than relying on memory:

```bash
hermes pr-review dogfood-score /tmp/run.json \
  --case CASE_ID \
  --quality post_worthy \
  --safe-to-post yes \
  --notes "Concise evidence-backed finding."

hermes pr-review dogfood-report evals/dogfood-runs/manual-scores.jsonl
```

Use `evals/dogfood-runs/beta-matrix.md` for the current evidence set and detailed scoring rubric.

## Webhook no-post canary

For an enabled repository with `postComment: false`:

1. verify `doctor`, service, Funnel, webhook, and status health;
2. open a low-risk pull request;
3. verify GitHub reports HTTP 202;
4. verify the delivery spool reaches a terminal processed state;
5. verify artifacts match the exact PR head;
6. push a synchronization commit and repeat;
7. verify polling deduplicates that reviewed head;
8. confirm zero automated GitHub comments.

## Posting canary

Posting mechanics and review quality are separate gates.

1. Prove the candidate finding in no-post mode.
2. Enable findings-only posting for one repository.
3. Confirm a clean review creates no new comment.
4. Confirm a positive review creates exactly one managed Hermes summary.
5. Push a fix and verify the same comment updates to zero findings.
6. Confirm repeat watch runs deduplicate the reviewed head.
7. Graduate to always-post summaries only after the repository-specific canary remains quiet.

Never enable normal posting for truncated diffs or unresolved noise/miss patterns.
