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

## Evidence-derived next work

- complete OpenDocument-family ingestion and truthful local native InPage `.inp` extraction for Urdu and other Perso-Arabic text (issue #38);
- real image OCR and audio transcription only when local processors and evidence-backed acceptance tests exist.

Collections, synchronization, vector infrastructure, GUI work, and broader provider support remain deferred until measured user work demonstrates that the simpler architecture is insufficient.
