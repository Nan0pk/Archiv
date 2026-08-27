# Roadmap

## Foundation

- product charter and public trust boundary;
- reproducible development environment;
- fast fork-safe CI;
- task, capability, evidence, and result contracts.

## Verified executor

- exact source-marker file task;
- immutable source hashes;
- external output validator;
- false-success regression tests;
- complete run ledger.

## Representative corpus

- generated TXT, PDF, DOCX, XLSX, PPTX, image, and audio fixtures;
- fixture manifest and provenance;
- normalized extraction schema.

## Search and citations

- SQLite metadata and FTS5;
- exact source locations;
- page, sheet, slide, image, and timestamp citation model.

## Office output

- template-based DOCX report;
- LibreOffice rendering and validation;
- citation appendix and source integrity checks.

## Workbench integration

- bounded MCP tools;
- pinned CoWork-OS compatibility surface;
- exact-task and false-completion regressions;
- no generic shell exposure.

## Offline alpha

- one-command local setup;
- local model adapter;
- backup and evidence export;
- application egress-denied acceptance run.

## User-ready Fedora alpha (0.1.0a2)

- no-checkout versioned Fedora installer;
- readable add, find, report, and status commands;
- automatic report task construction and independent verification;
- human-facing backup and restore lifecycle;
- no-network clean-user acceptance evidence.

## Real-work grounded-question alpha (0.1.0a3)

- `archiv ask` natural-language QA over local evidence with citation validation;
- generalized `archiv report` for real user objectives with model-assisted synthesis;
- `archiv model` surface (`status`, `configure`, `test`, `disable`);
- strict grounding protocol schema and validation;
- host acceptance script (`scripts/accept_host.py`);
- atomic versioned upgrade preserving durable home data and older versions.

## First real-work field trial

- deterministic public-safe multi-format benchmark;
- 22 evidence-grounding questions at a fixed evidence limit;
- retrieval, citation, completeness, honesty, latency, and usability measurements;
- explicitly opted-in private local mode with sanitized aggregate output;
- evidence-derived retrieval issue after the measured dominant failure was known;
- no speculative implementation during baseline collection.

## Explainable natural-language retrieval (0.1.0a4)

- keep `archiv find` literal and unchanged;
- deterministic local query derivation for `archiv ask` and model-assisted `archiv report`;
- bounded source-diverse merge, ranking, deduplication, and evidence-limit enforcement;
- versioned retrieval diagnostics and `--explain-retrieval`;
- private-safe aggregate diagnostics;
- full required-source recall for all 22 frozen benchmark questions at evidence limit 8;
- 22/22 valid citation packages, full deterministic completeness and honesty, and no source mutation;
- no vectors, embeddings, semantic reranker, model query rewriting, new daemon, or provider expansion.

## Bounded source location (0.1.0a5)

- `archiv source <object-sha256>` for an independently hash-validated immutable original;
- `archiv source --citation-file ... --citation-number N` for exact find, ask, and report citation envelopes;
- bounded path verification with traversal, symlink-escape, stale-citation, and object-substitution rejection;
- stable versioned JSON and readable human output;
- public-safe field-trial proof retaining only validation booleans;
- no shell execution, generic file browser, source execution, cloud link, upload, or storage-layout change.

## Local test console and tested format matrix (0.1.0a6)

The console remains available as `archiv ui --diagnostic`; it is distinct from
the user-oriented document-library application now launched by `archiv ui`.

- `archiv ui --diagnostic`: a minimal local test console derived from the real Typer command tree (issue #63);
- schema-driven command forms with native path pickers, choices, booleans, and validated numbers;
- one command at a time through shell-free argument vectors; complete retained output; live progress and exit status;
- verified output opening through the OS default handler only after Archiv-side revalidation;
- citation opening through the bounded source-location validator, never through arbitrary paths;
- clear failure when the desktop UI package or display is missing;
- committed, machine-readable, test-verified open-format compatibility matrix for every supported suffix (issue #37 core);
- public licence decision recorded and applied: Apache-2.0 (issue #2);
- no storage-layout, contract, or CLI behavior changes; the console stays replaceable.

## Readable format reporting and supported-interpreter safety

- `archiv formats`: a read-only human and JSON view of the committed, test-verified compatibility matrix, listing every supported suffix, what is extracted, the citation locators produced, and the known limits;
- the matrix ships as wheel package data, so an installed Archiv reports the same claims as a checkout;
- unsupported suffixes fail closed with the same reason the ingestion path gives, never an invented capability;
- `archiv ui` reports missing desktop support when either the `tkinter` package or the `_tkinter` extension is absent, instead of printing a traceback;
- the Fedora installer verifies Python >= 3.12 before building a virtual environment, so an unsupported interpreter fails with readable guidance rather than inside pip's resolver.

## Evidence-derived next work

The public field trial and real-world corpus notes rank gaps instead of treating every
document specification as a parser backlog. The committed machine-readable decision
is in `docs/format-compatibility.json`:

1. **Legacy `.doc` and `.rtf` ingestion — measure.** They occurred in the real-world
   corpus; acquire lawful content-bearing fixtures and quantify question impact first.
2. **Spreadsheet cross-cell retrieval — measure.** Field notes confirmed literal
   phrase misses across cell boundaries; benchmark row-aware retrieval before changing
   normalized output or citation locators.
3. **InPage layout, columns, frames, styles, and page geometry — defer** pending a
   lawful visual-ground-truth fixture set.
4. **Difficult Urdu typography/layout OCR — measure** for accuracy, language coverage,
   latency, and memory on target machines.
5. **WAV transcription and ODB record extraction — defer** pending measured bounded
   local processors, native locators, and acceptance thresholds.
6. **Macro-enabled `.docm` — defer** without measured demand and proof that macros stay
   inert; `.docm`, `.doc`, and `.rtf` remain explicit fail-closed rejections today.
7. **Collections, synchronization, and broad connectors — defer.** These are product
   workflows, not parsers, and did not outrank local ingestion/retrieval defects.

Every selected implementation must add lawful fixtures with provenance, explicit
parser bounds, a normalized-output contract, the best native locator available,
malformed-input tests, resource ceilings, and before/after source-hash validation.

- issue #38 is closed: OpenDocument-family ingestion and truthful native InPage `.inp` searchable-text extraction shipped in 0.1.0a5;
- InPage layout, frames, styles, and page-geometry reconstruction remain open (issue #53) pending lawful native fixtures and visual ground truth;
- visual OCR shipped in bounded form; engine evaluation and difficult-layout recovery continue under issue #54 once the local benchmark corpus and target-machine measurements exist;
- face detection and enrolled identity search (issue #55) require an implemented-and-measured local model baseline before any product slice;
- audio transcription only when a local processor and evidence-backed acceptance tests exist.

Collections, synchronization, vector infrastructure, richer product workflows, and broader provider support remain deferred until measured user work demonstrates that the simpler architecture is insufficient.
