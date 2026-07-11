# Hermes PR Review installed

The plugin is installed and enabled. Verify prerequisites before enabling a repository:

```bash
gh auth status
hermes pr-review doctor
```

Enable the first repository with a local checkout for trusted base-branch context:

```bash
hermes pr-review enable OWNER/REPO --local-repo /path/to/repo --json
```

GitHub comment posting is **disabled by default**. Keep it disabled until a no-post review and repository-specific webhook canary have been inspected.

For the managed Linux receiver, Tailscale Funnel, GitHub webhook, verification, and rollback flow, see:

<https://github.com/itsreverence/hermes-plugin-pr-review#zero-to-first-review-quickstart>
