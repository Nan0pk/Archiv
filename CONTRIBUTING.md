# Contributing

Archiv is at the foundation stage. Contributions should keep the core small, inspectable, local-first, and independently verifiable.

## Workflow

1. Open or select an issue with a concrete acceptance contract.
2. Create a focused branch or fork.
3. Add tests and external validation for changed behaviour.
4. Run `archiv doctor`, `ruff format --check .`, `ruff check .`, `pyright`, and `pytest`.
5. Open a pull request using the repository template.

## Public pull-request trust boundary

Fork and branch pull requests run only on GitHub-hosted runners with read-only permissions. They must not receive repository secrets, retain checkout credentials, or use `pull_request_target` to execute contributor code. Changes under `.github/workflows/` are checked by the repository's CI trust auditor. See [GitHub governance and CI trust boundary](docs/github-governance.md).

## Public data rule

Never submit real user documents, credentials, model keys, private archives, customer material, or proprietary samples. Replace them with generated fixtures containing unique markers.

## Design rule

Each capability must define what it reads, what it writes, what it is forbidden to change, what evidence it emits, and which independent validator decides success.

Large frameworks, databases, services, and autonomous planning layers require benchmark evidence that the simpler existing design is insufficient.
