# 2026-07-07 findings-only posting canary

Purpose: tiny docs-only PR used to verify `postComment: true` with `postFindingsOnly: true` stays quiet when the reviewer finds nothing.

Expected behavior:

- webhook review runs from the GitHub `pull_request.opened` event;
- local artifacts and watch state are written;
- no new GitHub comment is created when findings are zero.

Synchronized-head check: a second docs-only commit should also stay findings-only quiet.
