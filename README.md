# Archiv

Archiv is an early-stage, local-first knowledge-work system for preserving, searching, analysing, and producing evidence-backed documents while keeping source data and execution under the user's control.

## Product direction

Archiv will combine five deliberately separate layers:

1. immutable canonical originals;
2. rebuildable extraction and search indexes;
3. bounded deterministic capabilities;
4. optional model-assisted reasoning;
5. independent validation and provenance.

Models propose. Validators decide whether work succeeded.

## First milestone

The first milestone is intentionally small:

- run a deterministic file task;
- preserve source hashes;
- create a required artifact;
- validate it outside the executor;
- record complete evidence;
- expose the same bounded capability through a CLI and, later, MCP.

Archiv is **not** starting as another autonomous-agent framework, desktop Office editor, or all-in-one cloud platform.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
archiv doctor
pytest
```

A Codespaces configuration is included for a reproducible public development environment.

## Public-repository safety

Do not commit private documents, personal data, credentials, model keys, production databases, generated user archives, or proprietary test material. Development fixtures must be synthetic or explicitly redistributable. See [the public repository policy](docs/public-repository-policy.md).

## Project documents

- [Product charter](docs/product-charter.md)
- [Architecture](docs/architecture.md)
- [Execution contract](docs/execution-contract.md)
- [Definition of done](docs/definition-of-done.md)
- [Roadmap](docs/roadmap.md)
- [Harness evidence baseline](research/baseline/harness-verdict.md)

## Licensing

No open-source licence has been selected yet. Until a licence is added, copyright is reserved and the public source is available for inspection only. A licence decision is tracked separately so that publishing the repository does not accidentally grant terms the project has not chosen.

Product work proceeds through pull requests with evidence-producing acceptance checks.
