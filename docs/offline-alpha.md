# User-ready Fedora alpha (0.1.0a3)

Archiv `0.1.0a3` packages the grounded-question and cited-report local evidence workflow behind a simple everyday command surface. Installation may download Fedora and Python packages. After installation, adding, searching, asking, reporting, model configuration, verification, backup, and restore require no external network access, cloud login, telemetry endpoint, or online provider model.

## One-command Fedora installation

```bash
curl -fsSL https://raw.githubusercontent.com/Nan0pk/Archiv/main/tools/install-fedora.sh | bash
```

The installer:

- checks that the host is Fedora;
- installs Python, LibreOffice Writer, Poppler, and required transfer tools;
- resolves the requested Git ref to an immutable commit SHA;
- downloads and validates the source archive structure;
- records the exact commit and downloaded archive SHA-256 in `install.json`;
- installs into a versioned virtual environment under `~/.local/share/archiv-alpha/versions/`;
- exposes `archiv` and `archiv-mcp` through `~/.local/bin`;
- runs `archiv doctor` before reporting success.

No shell activation is required. Upgrading installs the new version, updates symlinks atomically, and preserves existing Archiv data and older versions.

## Everyday workflow

Create a small public-safe vault and exercise the complete interface:

```bash
archiv sample-vault "$HOME/Archiv-Sample"
archiv add "$HOME/Archiv-Sample"
archiv find "unique fixture marker"
archiv model configure --endpoint http://127.0.0.1:11434 --model llama3
archiv ask "What decisions were made and what remains unresolved?"
archiv report "Prepare a cited status report with risks and next actions"
archiv status
```

`archiv add` accepts one supported file or a directory, preserves each canonical original, creates normalized evidence, and refreshes the FTS5 search index automatically.

`archiv find` performs literal local full-text retrieval and prints readable source names, native locators, and excerpts. Every result is revalidated against the canonical original and normalized evidence before display.

`archiv ask` performs grounded natural-language QA over local evidence. It sends bounded evidence passages to the configured local model, strictly validates citation identifiers, detects missing evidence and contradictions, and stores durable run evidence under `ARCHIV_HOME/runs/ask/`.

`archiv report` creates a cited DOCX report for a real user objective. When a model is configured, it uses citation-constrained synthesis; when model is disabled or `--deterministic` is passed, it uses deterministic excerpt report generation. Every report is reopened, structurally validated, and rendered before reporting success.

`archiv status` shows the Archiv home, document and ingestion counts, index state, ask and report run outcomes, and explicit model configuration without changing state.

All everyday commands use readable output by default. Add `--json` for automation.

## Model configuration and safety

Model access is strictly loopback-only (HTTP on 127.0.0.1, localhost, ::1):

```bash
archiv model status
archiv model configure --endpoint http://127.0.0.1:11434 --model local-model-name
archiv model test
archiv model disable
```

Remote hosts, HTTPS tunnels, embedded credentials, query parameters, fragments, and unexpected paths are rejected. API keys are never stored in plain text configuration files (environment variables are supported). There is no cloud fallback.

## Host acceptance script

`scripts/accept_host.py` runs end-to-end acceptance checks over generated public-safe fixtures or an optional local folder, recording only safe operational metadata:

```bash
python scripts/accept_host.py --output acceptance-report.json
```

No private filenames or document contents are stored in acceptance reports.

## Backup, export, and restore

```bash
archiv backup "$HOME/archiv-backup.zip"
archiv export "$HOME/archiv-portable.zip"
archiv restore "$HOME/archiv-backup.zip" --home "$HOME/Restored-Archiv"
```

Archives include durable SQLite metadata, immutable originals, normalized evidence, ask and report run evidence, outputs, and configuration.

## Release evidence and limitations

Archiv is an alpha software release. Answer quality depends on document extraction, FTS5 retrieval, and the capabilities of the configured local model. Validated citations prevent fabricated source references, but independent verification remains necessary for critical decisions. Private local documents remain the user's responsibility.
