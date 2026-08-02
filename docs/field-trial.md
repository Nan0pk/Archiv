# Real-work field-trial methodology

## Purpose

The field trial measures Archiv's real retrieval, grounding, citation validation, reporting, privacy, and source-integrity behavior against a frozen machine-readable benchmark. Models propose answers; deterministic validators decide whether retrieval, citations, completeness, honesty, report structure, privacy, and source integrity succeeded.

## Public benchmark

Run:

```bash
python scripts/run_field_trial.py \
  --public \
  --output field-trial-artifacts
```

The harness deterministically generates the committed synthetic corpus, ingests it into an isolated Archiv home, starts a deterministic OpenAI-compatible loopback fixture, runs all 22 benchmark questions, generates one deterministic public DOCX report, verifies source hashes, and writes sanitized JSON and Markdown results.

The fake model isolates retrieval and validation behavior. It is not evidence of real-model quality.

### Recorded retrieval evidence

For every question the harness records:

- the retrieval strategy version;
- the bounded literal query variants derived locally from the objective;
- derived terms and triggered deterministic concepts;
- candidate and selected passage counts;
- selected-source scores and retrieval ranks;
- expected-source recall at the evidence limit;
- irrelevant-source count and evidence-limit displacement;
- whether required facts existed in normalized evidence;
- duplicate-passage contamination.

The benchmark calls the same natural-language retrieval service used by `archiv ask` and model-assisted `archiv report`. It does not substitute a separate test-only retriever.

### Recorded citation and answer evidence

The harness automatically checks that citation identifiers are well formed and belong to the retrieved package. Required-fact coverage, forbidden unsupported claims, contradiction acknowledgement, missing-evidence acknowledgement, and completeness are scored against explicit machine-readable expectations. These are deterministic benchmark checks, not subjective model judgements.

### Acceptance threshold for 0.1.0a4

At evidence limit 8, all 22 questions must retrieve every required source. All 22 citation packages must be structurally valid, no fabricated identifier may appear, all deterministic completeness and honesty checks must pass, and original hashes must remain unchanged.

The gate includes the six broad questions that exposed the original exact-phrase defect: architecture decisions, broad project status, completed work, unfinished work, dates/deadlines, and numerical claims.

### Usability and performance

The harness records indexing and per-question duration, report success, source-name and native-locator availability, and whether a bounded source-location command exists. Performance is reported rather than hidden behind a pass/fail threshold because the current corpus is intentionally small.

## Private local mode

Private processing is disabled unless the operator explicitly passes `--local-only`:

```bash
python scripts/run_field_trial.py \
  --corpus /path/to/local/disposable-copy \
  --questions /path/to/local/questions.json \
  --local-only
```

The harness copies supported files into a disposable local trial directory before ingestion and verifies the selected originals remain unchanged. Detailed evidence stays under the ignored `.archiv-field-trial/private/` tree.

The shareable summary excludes paths, filenames, questions, excerpts, derived queries, prompts, raw model output, source names, locators, source identifiers, and segment identifiers. It contains only aggregate counts, performance values, strategy version, source-integrity status, and explicit external blockers.

A real model is used only when both an explicit loopback `--model-endpoint` and `--model-name` are supplied. The harness does not autodetect, download, or silently select a provider. Without an explicit suitable endpoint, retrieval still runs and answer-quality testing is marked externally blocked.

## Privacy boundary

Public CI receives only generated fixtures and sanitized output. Private corpora, private questions, local databases, home paths, usernames, endpoint secrets, private retrieval diagnostics, and private reports must never be committed or uploaded as Actions artifacts.

Local `--explain-retrieval` output may show source names and native locators because it is intended for the operator on the same machine. That detailed explanation is not shareable telemetry and is excluded from sanitized summaries.

## Report inspection

The `Field trial` workflow installs LibreOffice Writer and Poppler, renders the public report, and uploads the DOCX, PDF, and page images. Structural validation remains independent of visual inspection. A release report is not accepted until the rendered pages are inspected for blank pages, clipping, overflow, unreadable tables, misplaced appendices, and leaked private data.

## Interpreting defects

Failures are classified in plain language: extraction, indexing, query construction, ranking, evidence limit, stale-version confusion, duplicate contamination, contradiction handling, model synthesis, unsupported claim, citation validation, rendering, source navigation, command friction, and latency/resource problems.

A new milestone is created only after measured results identify a remaining defect. A passing retrieval benchmark does not justify unrelated vectors, collections, synchronization, GUI work, provider expansion, or format support.
