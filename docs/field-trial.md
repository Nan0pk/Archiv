# Real-work field-trial methodology

## Purpose

The field trial measures Archiv's real retrieval, grounding, citation validation, reporting, privacy, source integrity, and bounded source-location behavior against a frozen machine-readable benchmark. Models propose answers; deterministic validators decide whether retrieval, citations, completeness, honesty, report structure, privacy, and source integrity succeeded.

## Public benchmark

Run:

```bash
python scripts/run_field_trial.py \
  --public \
  --output field-trial-artifacts
```

The harness deterministically generates the committed synthetic corpus, ingests it into an isolated Archiv home, starts a deterministic OpenAI-compatible loopback fixture, runs all 22 benchmark questions, generates one deterministic public DOCX report, verifies source hashes, and writes sanitized JSON and Markdown results.

The fake model isolates retrieval and validation behavior. It is not evidence of real-model quality.

### Retrieval, citation, and answer evidence

For every question the harness records the retrieval strategy version, bounded query variants, candidate and selected passage counts, expected-source recall, duplicate contamination, citation structure, deterministic required-fact coverage, missing-evidence honesty, contradiction acknowledgement, and unsupported claims.

At evidence limit 8, all 22 questions must retrieve every required source. All citation packages must be structurally valid, no fabricated identifier may appear, deterministic completeness and honesty checks must pass, and original hashes must remain unchanged.

### Bounded source-location evidence

After the benchmark, a separate public-safe probe:

1. creates one disposable text fixture;
2. ingests and finds its marker through the installed `archiv` command;
3. writes the resulting citation only inside the disposable directory;
4. resolves that citation through `archiv source`;
5. requires citation revalidation, original-hash revalidation, and read-only output;
6. discards the returned local path and all source metadata;
7. records only validation booleans in public results;
8. reruns the artifact privacy scan before workflow success.

The source-navigation defect is removed only when this probe succeeds.

## Private local mode

Private processing is disabled unless the operator explicitly passes `--local-only`. Detailed private evidence stays under the ignored local trial tree. The shareable summary excludes paths, filenames, questions, excerpts, derived queries, prompts, raw model output, source names, locators, source identifiers, and segment identifiers.

A real model is used only when both an explicit loopback endpoint and model name are supplied. The harness does not autodetect, download, or silently select a provider.

## Report inspection

The `Field trial` workflow renders the public report and uploads the DOCX, PDF, and page images. Structural validation remains independent of visual inspection. A release report is not accepted until the rendered pages are inspected for blank pages, clipping, overflow, unreadable tables, misplaced appendices, and leaked private data.

## Interpreting defects

Failures are classified in plain language: extraction, indexing, query construction, ranking, evidence limit, stale-version confusion, duplicate contamination, contradiction handling, model synthesis, unsupported claim, citation validation, rendering, source navigation, command friction, and latency/resource problems.

A new milestone is created only after measured results identify a remaining defect. A passing benchmark does not justify unrelated vectors, collections, synchronization, GUI work, provider expansion, or format support.
