# User-ready Fedora alpha

Archiv `0.1.0a2` packages the verified local evidence workflow behind a small everyday command surface. Installation may download Fedora and Python packages. After installation, adding, searching, reporting, verification, backup, and restore require no GitHub access, cloud login, telemetry endpoint, or online model.

## One-command Fedora installation

```bash
curl -fsSL https://raw.githubusercontent.com/Nan0pk/Archiv/main/tools/install-fedora.sh | bash
```

The installer:

- checks that the host is Fedora;
- installs Python, LibreOffice Writer, Poppler, and required transfer tools;
- resolves the requested Git ref to an immutable commit SHA;
- downloads and validates the source archive structure;
- records the exact commit and downloaded archive SHA-256;
- installs into a versioned virtual environment under `~/.local/share/archiv-alpha`;
- exposes `archiv` and `archiv-mcp` through `~/.local/bin`;
- runs `archiv doctor` before reporting success.

No shell activation is required. Use `--ref`, `--prefix`, or `--bin-dir` to override defaults. For an auditable two-step installation, download `tools/install-fedora.sh`, inspect it, and run it locally.

The compatibility command from a source checkout remains available:

```bash
bash tools/setup-fedora.sh
```

## Everyday workflow

Create a small public-safe vault and exercise the complete interface:

```bash
archiv sample-vault "$HOME/Archiv-Sample"
archiv add "$HOME/Archiv-Sample"
archiv find "unique fixture marker"
archiv report "unique fixture marker"
archiv status
```

`archiv add` accepts one supported file or a directory, preserves each canonical original, creates normalized evidence, and refreshes the FTS5 search index automatically. It reports new originals, duplicates, unsupported files skipped from a directory, and indexed document and passage counts.

`archiv find` performs literal local full-text retrieval and prints readable source names, native locators, and excerpts. Every result is revalidated against the canonical original and normalized evidence before display.

`archiv report` creates the bounded `cross-file-report` request internally. It generates a cited DOCX under the Archiv home, then independently reopens the archived request, source hashes, report, manifest, and citations. The command reports success only after verification. No YAML task file or remembered run ID is required for normal use.

`archiv status` shows the Archiv home, document and ingestion counts, index state, report outcomes, and explicit model policy without changing state.

All everyday commands use readable output by default. Add `--json` for automation.

## Advanced and compatibility commands

The lower-level evidence interfaces remain available for tests, integrations, and diagnosis:

```bash
archiv doctor
archiv ingest ./document.docx
archiv rebuild-derived <sha256>
archiv rebuild-search-index
archiv search "exact phrase"
archiv run tests/tasks/cross-file-report.yaml
archiv verify <run-id>
```

`archiv run` still accepts the JSON subset of YAML. A run receives a unique directory under `ARCHIV_HOME/runs/tasks/`; generated files remain under `ARCHIV_HOME/outputs/tasks/<run-id>/`.

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

These commands use readable summaries by default and accept `--json`. Archives include durable SQLite metadata, immutable originals, normalized evidence, task and MCP run evidence, outputs, and configuration. They exclude `indexes/` and `temporary/` because those are rebuildable. Every member is size- and SHA-256-validated before restore. Restore requires an empty destination, relocates recorded paths, reapplies read-only original permissions, and rebuilds the search index.

## Acceptance evidence

`tools/run-user-acceptance.sh` proves the human-facing journey without parsing JSON:

```text
sample-vault -> add -> status -> find -> report -> backup -> restore -> find
```

The `Offline alpha` workflow installs Archiv into a Fedora image, then runs both the machine-readable lifecycle acceptance and the human-facing acceptance with:

```text
docker run --network none --cap-drop ALL ...
```

The resulting logs, DOCX, backup, hashes, platform details, and duration are retained as deliberate release evidence for 30 days. This proves the application workflow does not need network access after installation; it does not claim Fedora or Python packages can be installed without package media.

## CoWork-OS equivalence

The pinned CoWork regression remains a separate boundary test. It launches `archiv-mcp` through CoWork's actual stdio transport, discovers the six bounded tools, searches, reads exact sources, generates and verifies a cited DOCX, retrieves run evidence, checks source immutability, and proves invalid or tampered paths cannot become structured success. Alpha changes retrigger both the pinned and current-upstream lanes; current upstream never changes the accepted lock.

## Release evidence

`python tools/build-release.py --output release-artifacts` creates:

- a reproducible wheel, built twice and compared byte-for-byte;
- a normalized tracked-source archive;
- `SHA256SUMS`;
- a CycloneDX 1.5 dependency SBOM;
- a release manifest containing the source commit and fixed source-date epoch.

The release remains an alpha. Back up important source material independently and do not expose Archiv to untrusted networks.
