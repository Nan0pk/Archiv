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
- at least 20 evidence-grounding questions;
- retrieval, citation, completeness, honesty, latency, and usability measurements;
- explicitly opted-in private local mode with sanitized aggregate output;
- one evidence-derived `0.1.0a4` issue after the measured dominant failure is known;
- no speculative `0.1.0a4` implementation during baseline collection.
