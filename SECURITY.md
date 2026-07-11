# Security Policy

## Supported versions

Until the first public release, only the latest commit on `main` is maintained. After release, security fixes will target the latest minor version unless a release note says otherwise.

## Reporting a vulnerability

Do not open a public issue containing webhook secrets, provider credentials, private repository data, raw webhook payloads, or review artifacts.

Use GitHub private vulnerability reporting when it is enabled for this repository. If that option is unavailable, contact the repository owner privately through their GitHub profile before sharing technical details.

Include the affected version, deployment shape, reproduction steps, and impact. Redact credentials, repository contents, local absolute paths, PR text, and artifact payloads.

## Relevant boundaries

- The receiver is intended to bind to loopback and sit behind an authenticated HTTPS ingress such as Tailscale Funnel.
- GitHub webhook bodies require SHA-256 HMAC validation before admission.
- Pull-request code is untrusted and is not executed by the reviewer.
- Review artifacts and service journals remain local but may contain repository metadata and review text.
- Posting and remote onboarding mutations are opt-in.

If a webhook secret may have been exposed, remove or disable the affected GitHub hook, replace the local secret, update the hook with the new secret, and restart the receiver before re-enabling deliveries.
