# User-ready Fedora alpha (0.1.0a5)

Archiv `0.1.0a5` packages explainable natural-language retrieval, bounded source-location verification, grounded questions, and cited-report workflows behind a simple everyday command surface. Installation may download Fedora and Python packages. After installation, adding, searching, asking, reporting, model configuration, verification, backup, and restore require no external network access, cloud login, telemetry endpoint, or online provider model.

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
archiv find "unique fixture marker" --json > matches.json
archiv source --citation-file matches.json --citation-number 1
archiv model configure --endpoint http://127.0.0.1:11434 --model llama3
archiv ask "What decisions were made and what remains unresolved?" --explain-retrieval
archiv report "Prepare a cited status report with risks and next actions" --explain-retrieval
archiv status
```

`archiv add` accepts one supported file or a directory, preserves each canonical original, creates normalized evidence, and refreshes the FTS5 search index automatically.

`archiv find` performs literal local full-text retrieval and prints readable source names, native locators, and excerpts. Every result is revalidated against the canonical original and normalized evidence before display.

`archiv source` accepts a lowercase canonical object SHA-256 or an explicit citation selected from a JSON citation, find result, ask result, or report manifest. It independently revalidates the citation, canonical hash, normalized metadata, and bounded storage path before returning a read-only local location. It never executes the source, follows an external symlink, opens a URL, or provides generic filesystem browsing.

`archiv ask` performs grounded natural-language QA over local evidence. It derives bounded query variants locally, merges and ranks results deterministically, sends only validated evidence passages to the configured local model, strictly validates citation identifiers, detects missing evidence and contradictions, and stores durable run and retrieval evidence under `ARCHIV_HOME/runs/ask/`.

Model-assisted `archiv report "objective"` uses the same natural-language retrieval package for model synthesis and report citations. Model-disabled, unreachable, timeout, and invalid-response conditions fail closed without a hidden fallback. Deterministic excerpt reporting occurs only when `--deterministic` is explicitly passed. Every successful report is reopened, structurally validated, and rendered before success is reported.

`--explain-retrieval` shows the local strategy, derived terms, recognized concepts, candidate count, selected sources, native locators, scores, and ranks. No model or network call is used to rewrite the query.

`archiv status` shows the Archiv home, document and ingestion counts, index state, ask and report run outcomes, and explicit model configuration without changing state.

All everyday commands use readable output by default. Add `--json` for automation.

## Model configuration and safety

Model access is strictly loopback-only over HTTP on `127.0.0.1`, `localhost`, or `::1`:

```bash
archiv model status
archiv model configure --endpoint http://127.0.0.1:11434 --model local-model-name
archiv model test
archiv model disable
```

Remote hosts, HTTPS tunnels, embedded credentials, query parameters, fragments, and unexpected paths are rejected. API keys are never stored in plain-text configuration files; environment variables are supported. There is no cloud fallback.

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

The frozen public field trial retrieves every required source for all 22 questions at evidence limit 8, with 22/22 structurally valid citation packages, zero fabricated identifiers, full deterministic completeness and honesty, unchanged original hashes, and a successful sanitized `archiv source` citation probe.

Archiv remains alpha software. The deterministic public fixture isolates retrieval and validation; it does not prove the quality of a user's chosen local model. Private local corpus/model testing remains an operator-run activity. Critical decisions still require human review of the preserved source content and context.
