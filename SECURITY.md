# Security policy

Archiv is pre-alpha software. Do not use it as the sole store for important data and do not expose it to untrusted networks.

## Reporting a vulnerability

Do not publish exploit details, credentials, private documents, or sensitive logs in a public issue.

Use GitHub private vulnerability reporting for this repository when available. If that surface is unavailable, open a minimal public issue stating only that a private security contact channel is required; omit technical exploit details until a private channel is established.

Response targets, dependency triage, signed-release procedure, and the independent-review
stable gate are documented in [Security maintenance](docs/security/maintenance.md) and the
[threat model](docs/security/threat-model.md).

The repository owner must verify that private vulnerability reporting is enabled under **Settings → Code security and analysis**. The effective target configuration and incident procedure are recorded in [GitHub governance and CI trust boundary](docs/github-governance.md).

## Public-repository constraints

- Pull-request workflows must not receive repository secrets.
- Pull-request workflows must use read-only permissions and GitHub-hosted runners.
- `pull_request_target` must not execute contributor-controlled code.
- Test data must be synthetic or explicitly redistributable.
- Local model files, user archives, keys, databases, and run ledgers must remain outside Git.
- A model's statement of success is never accepted as validation evidence.

## Diagnostics export privacy boundary

`archiv diagnostics-export SUPPORT.json` is user-invoked and shows the complete JSON
payload before asking permission to save it. The exporter uses an allow list: product,
Python and OS versions; installed dependency versions; schema versions; database
readability; aggregate ingestion/processing states; fixed error categories; and aggregate
validation outcomes. It never reads or emits environment values or configuration, and it
never emits document names, paths, hashes/identifiers, excerpts, questions, answers, model
prompts/responses/endpoints, credentials, timestamps, or raw error messages.

Residual risks remain: uncommon platform/dependency version strings may fingerprint a
machine; counts can reveal library size and failure patterns; and a future dependency or
schema change could introduce a defect. Users must inspect the on-screen preview and may
decline saving or sharing it. Support must not ask for the archive, database, configuration,
logs, screenshots containing documents, or environment dumps.
