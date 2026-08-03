# Archiv

Archiv is an early-stage, local-first knowledge-work system for preserving, searching, analysing, and producing evidence-backed documents while keeping source data and execution under the user's control.

## Product direction

Archiv combines five deliberately separate layers:

1. immutable canonical originals;
2. rebuildable extraction and search indexes;
3. bounded deterministic capabilities;
4. optional model-assisted reasoning;
5. independent validation and provenance.

Models propose. Validators decide whether work succeeded.

## Current implemented slice (0.1.0a5)

Archiv includes a human-facing Fedora command surface, a deterministic environment doctor, native searchable-text ingestion for InPage `.inp` documents, explainable natural-language retrieval, bounded source-location verification (`archiv source`), grounded QA over local evidence (`archiv ask`), model-assisted cited report generation (`archiv report`), loopback-only OpenAI-compatible model configuration (`archiv model`), immutable local ingestion, validated SQLite full-text retrieval, independently verified cited DOCX generation, a bounded local MCP server, and a pinned replaceable CoWork-OS workbench integration:

```bash
archiv add /path/to/documents
archiv ingest /path/to/urdu-document.inp
archiv model configure --endpoint http://127.0.0.1:11434 --model llama3
archiv ask "What decisions were made and what remains unresolved?"
archiv ask "What remains unfinished?" --explain-retrieval
archiv report "Prepare a cited status report with risks and next actions"
archiv status
```

The grounded-question journey runs over locally ingested Archiv evidence:

```text
user question
→ deterministic local query derivation
→ validated literal FTS searches
→ source-diverse merge, ranking and evidence limit
→ citation validation
→ bounded evidence package
→ configured local model
→ proposed answer
→ citation parsing and validation
→ independent verification
→ readable answer with sources
→ durable run and retrieval evidence
```

`archiv find` remains literal and predictable. `archiv source` accepts either a canonical object SHA-256 or one explicit citation from a JSON citation, find result, ask result, or report manifest; it revalidates the immutable original and citation before returning a bounded read-only path inside Archiv-controlled storage. Natural-language `ask` and model-assisted `report` derive bounded query variants locally, without a model, embedding service, vector database, network call, or background daemon. `--explain-retrieval` shows the terms, recognized concepts, selected sources, native locators, scores, and ranks used for the decision.

Ingestion validates supported inputs, stores read-only content-addressed originals, records processing in SQLite, and builds rebuildable search indexes. Native InPage100 and InPage300 files are parsed locally into stream/byte-offset text segments for search and grounded use; the immutable source is preserved, while page/frame/layout reconstruction is explicitly outside the current claim. Full-text search builds a separate replaceable FTS5 database and returns citations that are revalidated against the original and normalized hashes before use. Cited DOCX reports include exact source evidence and are reopened, structurally validated, and rendered through LibreOffice before success is reported.

The model interface is strictly bound to explicit loopback-only HTTP endpoints such as Ollama, LocalAI, or vLLM. Remote hosts, cloud fallbacks, HTTPS tunnels, and credential embedding are rejected.

The frozen public field trial retrieves every required source for all 22 benchmark questions at evidence limit 8, with 22/22 structurally valid citation packages and zero fabricated identifiers. See the [field-trial report](docs/field-trial-report.md) for scope and limitations.

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
archiv ingest "$HOME/Documents/urdu-document.inp"
archiv find "unique fixture marker" --json > matches.json
archiv source --citation-file matches.json --citation-number 1
archiv ask "What decisions were made?" --explain-retrieval
archiv report "Prepare a cited status report with risks and next actions"
archiv status
archiv backup "$HOME/archiv-backup.zip"
```

`add` refreshes search automatically. `find` shows readable verified literal matches. `source` locates one independently revalidated preserved original without arbitrary browsing or execution. `ask` runs grounded QA over locally retrieved evidence. `report` creates a cited DOCX report for a user objective and independently verifies it before reporting success. Add `--json` when machine-readable output is required.

Development setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
archiv doctor
pytest
```

## Public-repository safety

Do not commit private documents, personal data, credentials, model keys, production databases, generated user archives, or proprietary test material. Development fixtures must be synthetic or explicitly redistributable. See the [public repository policy](docs/public-repository-policy.md) and [GitHub governance and CI trust boundary](docs/github-governance.md).

## Project documents

- [Product charter](docs/product-charter.md)
- [Architecture](docs/architecture.md)
- [Execution contract](docs/execution-contract.md)
- [Immutable ingestion](docs/ingestion.md)
- [Native InPage ingestion](docs/inpage-ingestion.md)
- [Local search and citations](docs/search.md)
- [Bounded source location](docs/source-location.md)
- [Real-work field-trial method](docs/field-trial.md)
- [Measured field-trial report](docs/field-trial-report.md)
- [Cited DOCX reports](docs/reporting.md)
- [Bounded local MCP server](docs/mcp.md)
- [CoWork-OS integration](docs/cowork-os-integration.md)
- [User-ready Fedora alpha](docs/offline-alpha.md)
- [Hardware and performance notes](docs/hardware-and-performance.md)
- [GitHub governance and CI trust boundary](docs/github-governance.md)
- [Definition of done](docs/definition-of-done.md)
- [Roadmap](docs/roadmap.md)

## Licensing

No open-source licence has been selected yet. Until a licence is added, copyright is reserved and the public source is available for inspection only. A licence decision is tracked separately so that publishing the repository does not accidentally grant terms the project has not chosen.

Product work proceeds through pull requests with evidence-producing acceptance checks.
