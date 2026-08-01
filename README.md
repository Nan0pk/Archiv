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

## Current implemented slice

Archiv includes a human-facing Fedora command surface, a deterministic environment doctor, the exact source-marker execution contract, immutable local ingestion, validated SQLite full-text retrieval, independently verified cited DOCX generation, a bounded local MCP server, and a pinned replaceable CoWork-OS workbench integration:

```bash
mkdir -p /tmp/archiv-probe
printf 'ARCHIV-DEMO-MARKER\n' > /tmp/archiv-probe/source.txt
archiv source-marker --workspace /tmp/archiv-probe

archiv ingest ./document.docx
archiv rebuild-derived <sha256>
archiv rebuild-search-index
archiv search "exact phrase"
archiv generate-report "exact phrase" ./evidence-report.docx
archiv verify-report ./evidence-report.docx ./evidence-report.docx.manifest.json

ARCHIV_HOME=/absolute/path/to/archiv-home archiv-mcp
```

The source-marker command creates exactly `outputs/probe.txt`, preserves `source.txt`, validates both outside the executor, and records machine-readable evidence under `runs/<run-id>/`.

Ingestion validates supported inputs, stores one read-only content-addressed original, records imports and processing in SQLite, and creates rebuildable normalized data outside the repository. Full-text search builds a separate replaceable FTS5 database and returns citations that are revalidated against the original and normalized hashes before use. Cited DOCX reports include exact source evidence and are reopened, structurally validated, and optionally rendered through LibreOffice before success is reported.

The MCP server exposes only six task-specific local tools over stdio. It has no shell, URL fetcher, arbitrary output path, or network tool. Every MCP call records append-only request and terminal result evidence under `ARCHIV_HOME/runs/mcp/`.

CoWork-OS is integrated only as a replaceable MCP workbench. Archiv pins one reviewed upstream revision, tests that revision with CoWork's actual stdio transport, tests current upstream separately without adopting it, and keeps canonical storage, execution status, citations, validation, and evidence outside the workbench.

## Quick start

Install on Fedora without cloning the repository or activating a virtual environment:

```bash
curl -fsSL https://raw.githubusercontent.com/Nan0pk/Archiv/main/tools/install-fedora.sh | bash
```

The installer resolves `main` to one immutable commit, installs that exact source under `~/.local/share/archiv-alpha/versions/`, records the commit and downloaded archive hash in `install.json`, and exposes `archiv` through `~/.local/bin`.

Use the everyday interface:

```bash
archiv sample-vault "$HOME/Archiv-Sample"
archiv add "$HOME/Archiv-Sample"
archiv find "unique fixture marker"
archiv report "unique fixture marker"
archiv status
archiv backup "$HOME/archiv-backup.zip"
```

`add` refreshes search automatically. `find` shows readable verified citations. `report` constructs the bounded task request internally, creates a cited DOCX, and independently verifies it before reporting success. Add `--json` when machine-readable output is required.

Development setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
archiv doctor
pytest
```

A Codespaces configuration is included for a reproducible public development environment.

## Public-repository safety

Do not commit private documents, personal data, credentials, model keys, production databases, generated user archives, or proprietary test material. Development fixtures must be synthetic or explicitly redistributable. See the [public repository policy](docs/public-repository-policy.md) and [GitHub governance and CI trust boundary](docs/github-governance.md).

## Project documents

- [Product charter](docs/product-charter.md)
- [Architecture](docs/architecture.md)
- [Execution contract](docs/execution-contract.md)
- [Immutable ingestion](docs/ingestion.md)
- [Local search and citations](docs/search.md)
- [Cited DOCX reports](docs/reporting.md)
- [Bounded local MCP server](docs/mcp.md)
- [CoWork-OS integration](docs/cowork-os-integration.md)
- [User-ready Fedora alpha](docs/offline-alpha.md)
- [Hardware and performance notes](docs/hardware-and-performance.md)
- [GitHub governance and CI trust boundary](docs/github-governance.md)
- [Definition of done](docs/definition-of-done.md)
- [Roadmap](docs/roadmap.md)
- [Harness evidence baseline](research/baseline/harness-verdict.md)

## Licensing

No open-source licence has been selected yet. Until a licence is added, copyright is reserved and the public source is available for inspection only. A licence decision is tracked separately so that publishing the repository does not accidentally grant terms the project has not chosen.

Product work proceeds through pull requests with evidence-producing acceptance checks.
