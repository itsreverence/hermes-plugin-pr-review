# Agent Notes

This repo is a local-first external Hermes PR review plugin.

## Boundaries

- Do not treat this as a merge-ready Hermes core contribution.
- Keep the workflow opt-in and local-artifact-first.
- Do not post GitHub comments unless explicitly testing `--post-comment`.
- Do not execute PR code by default.
- Load reviewer config/docs from the trusted base branch, not the PR branch.
- Avoid maintainer-specific defaults in reusable plugin code.

## Verification

Use a local Hermes checkout when developing against unreleased plugin APIs:

```bash
export HERMES_AGENT_SRC=/path/to/hermes-agent
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m pytest tests/plugins -q
PYTHONPATH="$PWD:$HERMES_AGENT_SRC" python -m py_compile plugins/pr_review/*.py
```

For live no-post dogfood:

```bash
hermes pr-review review OWNER/REPO#123 --json
```

Record useful/noisy/missed findings in future eval notes rather than turning one-off preferences into global rules.
