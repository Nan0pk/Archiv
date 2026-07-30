# Security policy

Archiv is pre-alpha software. Do not use it as the sole store for important data and do not expose it to untrusted networks.

## Reporting a vulnerability

Do not publish exploit details, credentials, private documents, or sensitive logs in a public issue.

Use GitHub private vulnerability reporting for this repository when available. If that surface is unavailable, open a minimal public issue stating only that a private security contact channel is required; omit technical exploit details until a private channel is established.

The repository owner must verify that private vulnerability reporting is enabled under **Settings → Code security and analysis**. The effective target configuration and incident procedure are recorded in [GitHub governance and CI trust boundary](docs/github-governance.md).

## Public-repository constraints

- Pull-request workflows must not receive repository secrets.
- Pull-request workflows must use read-only permissions and GitHub-hosted runners.
- `pull_request_target` must not execute contributor-controlled code.
- Test data must be synthetic or explicitly redistributable.
- Local model files, user archives, keys, databases, and run ledgers must remain outside Git.
- A model's statement of success is never accepted as validation evidence.
