# Archiv public field-trial report

## Scope

Archiv `0.1.0a3` was tested without retrieval tuning against 12 generated public-safe documents and 22 machine-readable questions. The corpus covers TXT, Markdown, PDF, DOCX, XLSX, and PPTX evidence; current and superseded versions; contradictions; unresolved actions; dates; numbers; duplicates; irrelevant lexical overlap; missing evidence; and multi-source synthesis.

The deterministic OpenAI-compatible loopback fixture isolates Archiv's retrieval, grounding, citation validation, and reporting behavior. It is not evidence of real-model synthesis quality.

## Measured results

Public workflow run `30719864845` produced artifact `8824523536` from head `fc093886f002f66249b4043543e3093ae97557ee`.

| Measure | Result |
|---|---:|
| Corpus documents | 12 |
| Benchmark questions | 22 |
| Evidence limit | 8 |
| Questions with every required source retrieved | 16 |
| Questions with a required-source miss | 6 |
| Mean required-source recall | 0.7273 |
| Structurally valid citation results | 22/22 |
| Fabricated or out-of-package citation identifiers | 0 |
| Fully complete answers under the deterministic rubric | 16/22 |
| Mean completeness score | 0.7273 |
| Unsupported benchmark claims | 0 |
| Indexing duration | 753.12 ms |
| Median end-to-end question duration | 443.8 ms |
| Total duration for 22 questions | 9663.89 ms |

The six retrieval failures were `Q02`, `Q12`, `Q17`, `Q18`, `Q20`, and `Q21`: architecture decisions, broad project status, completed work, unfinished work, dates/deadlines, and numerical claims. In every case the expected facts existed in normalized evidence, but the current implementation submitted the complete natural-language question as one exact FTS phrase and retrieved no required source.

## Citation integrity and honesty

All 22 structural citation checks passed. No malformed, fabricated, or non-retrieved citation identifier appeared. The deterministic fixture did not produce an unsupported benchmark claim.

When retrieval returned no evidence, Archiv failed closed and acknowledged insufficient evidence instead of inventing an answer. That behavior is honest, but six answers remained unusable because the retrieval layer failed before synthesis.

## Confirmed defects

### Query construction failure — high severity

- **Observed:** six of 22 ordinary natural-language questions missed required evidence already present in normalized documents.
- **Expected:** broad everyday questions should retrieve the relevant passages without requiring the user to guess an exact stored phrase.
- **Frequency:** 6/22 questions.
- **Impact:** incomplete answers and false impressions that the corpus lacks evidence.
- **Smallest corrective layer:** deterministic query construction before the existing FTS retrieval.

### Source navigation friction — medium severity

- **Observed:** citations contain source names and native locators, but no bounded command helps a normal user locate the preserved source.
- **Expected:** the user can move from a citation to the original source and location without manually exploring Archiv storage.
- **Frequency:** every cited answer.
- **Impact:** independent verification is slower than necessary.
- **Smallest corrective layer:** a safe source-location command; this is secondary to retrieval reliability.

### Blank report page — fixed

The first rendered sample inserted an empty page before the source appendix. The field-trial PR replaced the empty page-break paragraph with `page_break_before` on the appendix heading and added a regression test. The final rerender contains three populated pages with no blank page.

## Report verification

The sample DOCX was reopened and structurally validated, rendered through LibreOffice, converted to PDF and PNG pages, and inspected page by page.

- Page 1: title, executive summary, source table, five findings, citations, and locations are readable.
- Page 2: source appendix begins immediately; tables and hashes fit without clipping.
- Page 3: remaining appendix, validated excerpt, provenance, model identity, and generation policy are readable.
- Original fixture hashes remained unchanged.
- No unsupported source reference was found.
- Artifact privacy scan found no private data.

## Private local trial

A real private-corpus/model trial could not be executed from the GitHub environment because no user-selected disposable private corpus and no explicit suitable local loopback model endpoint were accessible. The harness supports explicit `--local-only` processing and generates a redacted aggregate summary, but this external blocker is recorded rather than replaced with invented results.

The GitHub environment also cannot verify the user's Fedora installation, `archiv doctor`, current local model configuration, or an already-running loopback endpoint. Repository version and release state were verified; host state requires execution on the Fedora machine.

## Decision for 0.1.0a4

The evidence does not support collections or incremental synchronization as the next release. The dominant failure occurs earlier: natural-language objectives do not reliably reach existing evidence.

Issue [#36 — Make natural-language evidence retrieval reliable and explainable](https://github.com/Nan0pk/Archiv/issues/36) defines `0.1.0a4` around deterministic local query derivation and privacy-safe retrieval diagnostics. Collections, vector databases, model-generated query rewriting, GUI work, and provider expansion are explicit non-goals.

## Administrative issues

Licence issue #2 remains open because no deliberate owner licence decision was recorded. Repository/CI trust issue #10 remains open because owner-interface settings must satisfy its acceptance criteria. Neither administrative issue was allowed to redefine or block the field-trial evidence.
