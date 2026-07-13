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

Follow [CONTRIBUTING.md](CONTRIBUTING.md) for the local gate and pull-request expectations. Use [docs/TESTING.md](docs/TESTING.md) for no-post dogfood and posting canaries. Keep operational commands and recovery guidance in [docs/OPERATIONS.md](docs/OPERATIONS.md).
