# Archiv public field-trial report

## Scope

Archiv `0.1.0a5` was evaluated against the frozen public-safe corpus of 12 generated documents and 22 machine-readable questions spanning TXT, Markdown, PDF, DOCX, XLSX, and PPTX evidence. The deterministic loopback fixture isolates retrieval, grounding, citation validation, completeness, honesty, reporting, and source-location behavior. It does not measure the quality of an arbitrary real local model.

## Measured result

Workflow run `30748825568` produced artifact `8833770549` from head `c7d7d4ccf783adae683dd4f0b21aabcda4f7d7ed`, with artifact digest `sha256:60d73fa371063df484211eafd056153c42553f40754992bd47abedca29d57ed4`.

| Measure | Result |
|---|---:|
| Corpus documents | 12 |
| Benchmark questions | 22 |
| Evidence limit | 8 |
| Questions with every required source | 22/22 |
| Required-source misses | 0 |
| Mean required-source recall | 1.0 |
| Structurally valid citation packages | 22/22 |
| Fabricated or out-of-package identifiers | 0 |
| Fully complete deterministic answers | 22/22 |
| Honesty checks passed | 22/22 |
| Unsupported claims | 0 |
| Indexing duration | 951.56 ms |
| Median question duration | 659.32 ms |
| Total duration for 22 questions | 14,494.1 ms |

## Bounded source location

The post-benchmark public-safe probe successfully used `archiv source` on an actual validated citation. It required:

- citation revalidation;
- immutable original SHA-256 revalidation;
- a path bounded inside Archiv-controlled storage;
- read-only output;
- no retained absolute path, source filename, fixture marker, excerpt, prompt, or raw command output in the public artifact.

The resulting public summary contains only validation booleans. The previous source-navigation-friction defect is therefore resolved, and the measured defect list is empty.

## Security and compatibility

The dedicated regression suite rejects malformed or uppercase object identifiers, traversal attempts, symlink escape, object substitution, stale or fabricated citations, ambiguous citation selection, and missing references. The command does not execute a source, invoke a shell or desktop opener, follow an external link, provide arbitrary browsing, upload content, or modify storage.

Existing literal search, grounded ask, model-assisted report, Office rendering, MCP, CoWork, backup/restore, installer upgrade, privacy, and offline-alpha checks remain part of exact-head validation.

## Report verification

The sample DOCX was reopened and structurally validated, rendered to PDF and three PNG pages, and inspected page by page:

- page 1: title, summary, source table, findings, citations, and locations are readable;
- page 2: the source appendix and hashes are readable with no clipping;
- page 3: the remaining appendix and provenance are readable;
- no blank page, overflow, broken table, unsupported citation, or private data was found.

## Remaining limitations and next work

A real private-corpus/model trial was not fabricated because GitHub had no user-selected disposable private corpus or explicit suitable local loopback model endpoint. Host-specific Fedora checks still require execution on that machine.

The next evidence-derived product track is issue #38: complete OpenDocument-family ingestion and truthful local native InPage `.inp` extraction. Collections, synchronization, vectors, GUI work, and provider expansion remain deferred without measured need.
