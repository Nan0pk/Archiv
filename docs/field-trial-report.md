# Archiv public field-trial report

## Scope

Archiv `0.1.0a4` was tested against the same frozen public-safe benchmark used to identify the `0.1.0a3` retrieval defect: 12 generated documents and 22 machine-readable questions at an evidence limit of 8. The corpus covers TXT, Markdown, PDF, DOCX, XLSX, and PPTX evidence; current and superseded versions; contradictions; unresolved actions; dates; numbers; duplicates; irrelevant lexical overlap; missing evidence; and multi-source synthesis.

The deterministic OpenAI-compatible loopback fixture isolates Archiv's retrieval, grounding, citation validation, and reporting behavior. It is not evidence of real-model synthesis quality.

## Measured results

Acceptance workflow run `30744859517` produced artifact `8832544059` from implementation head `029e3d96a44fb010999747615fa5f2131dc56ef1`.

| Measure | Baseline `0.1.0a3` | `0.1.0a4` candidate |
|---|---:|---:|
| Corpus documents | 12 | 12 |
| Benchmark questions | 22 | 22 |
| Evidence limit | 8 | 8 |
| Questions with every required source retrieved | 16 | **22** |
| Questions with a required-source miss | 6 | **0** |
| Mean required-source recall | 0.7273 | **1.0** |
| Structurally valid citation results | 22/22 | **22/22** |
| Fabricated or out-of-package citation identifiers | 0 | **0** |
| Fully complete answers under the deterministic rubric | 16/22 | **22/22** |
| Honesty checks passed | 22/22 | **22/22** |
| Mean completeness score | 0.7273 | **1.0** |
| Unsupported benchmark claims | 0 | **0** |
| Indexing duration | 882.31 ms | 854.24 ms |
| Median end-to-end question duration | 440.88 ms | 558.67 ms |
| Total duration for 22 questions | 9600.77 ms | 12334.73 ms |

The six former failures—`Q02`, `Q12`, `Q17`, `Q18`, `Q20`, and `Q21`—now retrieve every required source. The full 22-question acceptance test is committed as a regression gate, so a future change cannot silently restore the exact-phrase failure.

The broader deterministic retrieval strategy increased median question duration by about 118 ms in this CI run while removing all required-source misses. No new daemon, database, model call, network dependency, vector index, or embedding model was introduced.

## What changed

`archiv find` remains literal and continues to use the original validated FTS phrase behavior.

Natural-language `archiv ask` and model-assisted `archiv report` now:

1. derive a bounded local set of literal query variants from the objective;
2. add deterministic concept queries only for recognized question classes;
3. call the existing validated FTS search primitive for each variant;
4. merge candidates by immutable source object;
5. rank and deduplicate deterministically;
6. enforce the requested evidence limit;
7. record a versioned retrieval decision with selected sources, locators, scores, ranks, and matched variants.

`--explain-retrieval` exposes the local decision in readable form. JSON run evidence includes the versioned diagnostics. Sanitized diagnostics omit source names, paths, filenames, locators, questions, excerpts, raw queries, prompts, model output, and source or segment identifiers.

## Citation integrity and honesty

All 22 structural citation checks passed. No malformed, fabricated, stale, or non-retrieved citation identifier appeared. The deterministic fixture produced no unsupported benchmark claim. Missing-evidence and contradiction questions remained honest after retrieval expansion.

Citation and excerpt integrity checks were not weakened. Every selected passage is still independently revalidated against the immutable original and normalized document before it enters a model prompt or report.

## Remaining measured defect

### Source navigation friction — medium severity

- **Observed:** citations contain source names and native locators, but no bounded command helps a normal user locate the preserved source.
- **Expected:** the user can move from a validated citation to the original source and location without manually exploring Archiv storage.
- **Frequency:** every cited answer.
- **Impact:** independent verification is slower than necessary.
- **Smallest corrective layer:** a safe, read-only source-location command.

This is tracked separately in issue [#40](https://github.com/Nan0pk/Archiv/issues/40). It is not part of the retrieval repair and does not delay `0.1.0a4`.

## Report verification

The sample DOCX was reopened and structurally validated, rendered through LibreOffice, converted to PDF and PNG pages, and inspected page by page.

- Page 1: title, executive summary, source table, five findings, citations, and locations are readable.
- Page 2: source appendix begins immediately; tables and hashes fit without clipping.
- Page 3: remaining appendix, validated excerpt, provenance, model identity, and generation policy are readable.
- No blank page, clipping, overflow, or unreadable appendix content was found.
- Original fixture hashes remained unchanged.
- No unsupported source reference was found.
- Artifact privacy scan found no private data.

## Private local trial

A real private-corpus/model trial could not be executed from the GitHub environment because no user-selected disposable private corpus and no explicit suitable local loopback model endpoint were accessible. The harness supports explicit `--local-only` processing and creates a redacted aggregate summary, but this external blocker is recorded rather than replaced with invented results.

The GitHub environment also cannot verify the user's Fedora installation, `archiv doctor`, current local model configuration, or an already-running loopback endpoint. Repository version and release state can be verified in CI; host state requires execution on the Fedora machine.

## Release decision

The evidence supports `0.1.0a4` as a narrow retrieval-reliability release. It closes the measured dominant failure without changing literal search, citation identity, storage layout, model policy, or the offline trust boundary.

OpenDocument/InPage support remains a separate compatibility track in issue [#38](https://github.com/Nan0pk/Archiv/issues/38). Source navigation is the next evidence-derived usability task in issue [#40](https://github.com/Nan0pk/Archiv/issues/40). Collections, vectors, model-generated query rewriting, GUI work, and provider expansion remain unsupported by current evidence.

## Administrative issues

Licence issue #2 remains open because no deliberate owner licence decision was recorded. Repository/CI trust issue #10 remains open because owner-interface settings must satisfy its acceptance criteria. Neither administrative issue was allowed to redefine the field-trial evidence.
