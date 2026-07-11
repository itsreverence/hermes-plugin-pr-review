# Changelog

All notable changes to Hermes PR Review are documented here.

## 0.2.0 — Public beta

- Add actionable `doctor` diagnostics with JSON output and repair steps.
- Add managed Linux user-systemd receiver install, status, logs, and safe removal.
- Pin the active Hermes profile home in the generated service and preserve prior units on failed updates.
- Add plan/apply Tailscale Funnel setup plus scoped port and public receiver verification.
- Add plan/apply GitHub webhook setup, explicit hook adoption, owned-ID status/removal, and secret-safe API transport.
- Add repository disablement and explicit persisted CodeGraph-launcher clearing.
- Add public quickstart, rollback, privacy, architecture, and operator documentation.
- Add secure after-install guidance at the plugin installation boundary.
- Add Python 3.11/3.12 CI.
- Prove public anonymous install, force-reinstall update, pinned rollback,
  restore, uninstall, and no-post webhook opened/synchronize/deduplication flows.

## 0.1.0 — Experimental local-first reviewer

- Initial Hermes-native review, local artifact, CodeGraph context, watch, webhook receiver, delivery spool, and recovery workflows.
