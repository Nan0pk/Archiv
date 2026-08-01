# Real-work field-trial methodology

## Purpose

The field trial measures Archiv `0.1.0a3` as it exists before tuning. Models propose answers; deterministic validators decide whether retrieval, citations, completeness, honesty, report structure, privacy, and source integrity succeeded.

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

- the exact derived query used by the current implementation;
- selected passage count and retrieval ranks;
- expected-source recall at the evidence limit;
- irrelevant-source count and evidence-limit displacement;
- whether required facts existed in normalized evidence;
- duplicate-passage contamination.

### Recorded citation and answer evidence

The harness automatically checks that citation identifiers are well formed and belong to the retrieved package. Required-fact coverage, forbidden unsupported claims, contradiction acknowledgement, missing-evidence acknowledgement, and completeness are scored against explicit machine-readable expectations. These are deterministic benchmark checks, not subjective model judgements.

### Usability and performance

The harness records indexing, model-test, and per-question duration, command count, report success, source-name and native-locator availability, and whether a safe source-opening command exists.

## Private local mode

Private processing is disabled unless the operator explicitly passes `--local-only`:

```bash
python scripts/run_field_trial.py \
  --corpus /path/to/local/disposable-copy \
  --questions /path/to/local/questions.json \
  --local-only
```

The harness copies supported files into a disposable local trial directory before ingestion and verifies the selected originals remain unchanged. Detailed evidence stays under the ignored `.archiv-field-trial/private/` tree. The shareable summary excludes paths, filenames, questions, excerpts, prompts, raw model output, and source names.

A real model is used only when both an explicit loopback `--model-endpoint` and `--model-name` are supplied. The harness does not autodetect, download, or silently select a provider. Without an explicit suitable endpoint, retrieval still runs and answer-quality testing is marked externally blocked.

## Privacy boundary

Public CI receives only generated fixtures and sanitized output. Private corpora, private questions, local databases, home paths, usernames, endpoint secrets, and private reports must never be committed or uploaded as Actions artifacts.

## Report inspection

The `Field trial` workflow installs LibreOffice Writer and Poppler, renders the public report, uploads the DOCX/PDF/page images, and retains them for manual page-by-page visual inspection. Structural validation remains independent of visual inspection.

## Interpreting defects

Failures are classified in plain language: extraction, indexing, query construction, ranking, evidence limit, stale-version confusion, duplicate contamination, contradiction handling, model synthesis, unsupported claim, citation validation, rendering, source navigation, command friction, and latency/resource problems. The next release issue is created only after measured results identify the dominant cluster.
