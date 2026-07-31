# First offline alpha

Archiv `0.1.0a1` packages the implemented local evidence workflow into a Fedora-first alpha. Installation may download Fedora and Python packages. After installation, the demonstrated workflow requires no GitHub access, cloud login, telemetry endpoint, or online model.

## Fedora setup

From an Archiv source checkout:

```bash
bash tools/setup-fedora.sh
source "$HOME/.local/share/archiv-alpha/activate"
```

The setup installs Python, LibreOffice Writer, and Poppler, creates an isolated virtual environment, installs Archiv, and exposes `archiv` and `archiv-mcp`. Use `--prefix /absolute/path` to choose a different installation root.

## Packaged sample

Create a small public-safe vault without downloading anything:

```bash
archiv sample-vault "$HOME/Archiv-Sample"
archiv ingest "$HOME/Archiv-Sample"
archiv search "unique fixture marker"
```

Directory ingestion walks supported files in deterministic path order, preserves each canonical original, and rebuilds the local FTS5 index after the batch.

## Required end-to-end demonstration

From the source checkout:

```bash
archiv doctor
archiv ingest tests/fixtures/representative-corpus
archiv search "unique fixture marker"
archiv run tests/tasks/cross-file-report.yaml
archiv verify <run-id>
```

`archiv run` accepts the JSON subset of YAML. The first task type is deliberately narrow: `cross-file-report`. A run receives a unique directory under `ARCHIV_HOME/runs/tasks/`; generated files remain under `ARCHIV_HOME/outputs/tasks/<run-id>/`. Terminal success requires:

- validated search citations;
- a generated DOCX and manifest;
- independent package, citation, source-hash, PDF, and page-image validation when rendering is requested;
- unchanged canonical originals;
- request and terminal result evidence.

`archiv verify` does not trust the original process or task file. It reopens the archived request evidence, source hashes, DOCX, manifest, and citations.

## Model policy

The alpha report path is deterministic and defaults to no model. Absence of a model configuration means `adapter: disabled`; Archiv never selects a provider automatically.

```bash
archiv model show
archiv model disable
archiv model configure-loopback \
  --endpoint http://127.0.0.1:11434 \
  --model local-model-name
```

The only initial model adapter is OpenAI-compatible HTTP on explicit loopback addresses. HTTPS or non-loopback hosts are rejected. A failed local request is an error; there is no cloud or alternate-model fallback. The model adapter is optional and does not determine report validation.

## Backup, export, and restore

```bash
archiv backup "$HOME/archiv-backup.zip"
archiv export "$HOME/archiv-portable.zip"
archiv restore "$HOME/archiv-backup.zip" --home "$HOME/Restored-Archiv"
```

The archive includes durable SQLite metadata, immutable originals, normalized evidence, task/MCP run evidence, outputs, and configuration. It excludes `indexes/` and `temporary/` because they are rebuildable. Every member is size- and SHA-256-validated before restore. Restore requires an empty destination, relocates recorded paths, reapplies read-only original permissions, and rebuilds the search index.

## Egress-denied acceptance

`tools/run-offline-acceptance.sh` executes doctor, directory ingestion, search, task run, verification, backup, restore, restored search, and restored verification. The `Offline alpha` workflow builds a Fedora image with dependencies while network is available, then runs this script with:

```text
docker run --network none --cap-drop ALL ...
```

The resulting JSON, DOCX backup, hashes, platform details, and duration are retained as deliberate release evidence for 30 days. This proves the application workflow does not need network access after installation; it does not claim the operating system itself can be installed without package media.

## CoWork-OS equivalence

The pinned CoWork regression remains a separate boundary test. It launches `archiv-mcp` through CoWork's actual stdio transport, discovers the six bounded tools, searches, reads exact sources, generates and verifies a cited DOCX, retrieves run evidence, checks source immutability, and proves invalid/tampered paths cannot become structured success. Alpha changes retrigger both the pinned and current-upstream lanes; current upstream never changes the accepted lock.

## Release evidence

`python tools/build-release.py --output release-artifacts` creates:

- a reproducible wheel, built twice and compared byte-for-byte;
- a normalized tracked-source archive;
- `SHA256SUMS`;
- a CycloneDX 1.5 dependency SBOM;
- a release manifest containing the source commit and fixed source-date epoch.

The release remains pre-alpha. Back up important source material independently and do not expose Archiv to untrusted networks.
