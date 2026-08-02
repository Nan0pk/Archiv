# Local full-text retrieval and citations

Archiv uses SQLite FTS5 over normalized segments produced by immutable ingestion. The index is intentionally small, local, offline, and completely rebuildable.

## Literal search

```bash
archiv rebuild-search-index
archiv search "ARCHIV-DOCX-MARKER-2026"
archiv search "quarterly finding" --source-name report.pdf --kind pdf
archiv find "quarterly finding"
```

`archiv search` and the human-facing `archiv find` treat the supplied text as a literal phrase rather than exposing raw FTS query syntax. Optional low-level search filters match exact source name, media type, normalized kind, or object SHA-256.

The index is stored at `ARCHIV_HOME/indexes/search.sqlite3`. Deleting it does not delete originals, normalized documents, metadata, or provenance. Re-run `archiv rebuild-search-index` to recreate it atomically.

## Natural-language evidence retrieval

`archiv ask` and model-assisted `archiv report` accept ordinary user objectives rather than requiring the user to guess an exact stored phrase.

```bash
archiv ask "What remains unfinished?"
archiv ask "What dates or deadlines are established?" --explain-retrieval
archiv report "Prepare a current project-status report" --explain-retrieval
```

Natural-language retrieval remains deterministic and local:

1. preserve the exact objective as one query attempt;
2. derive a bounded set of literal phrases and meaningful terms;
3. add fixed concept queries only when the objective contains recognized triggers;
4. run each variant through the existing validated FTS search primitive;
5. merge candidates by immutable object SHA-256;
6. rank sources by deterministic query weights and FTS position;
7. choose one best passage per source and enforce the evidence limit.

The strategy performs no model query rewriting, embeddings, vector search, network call, download, or background service. The current strategy identifier is `deterministic-literal-expansion-v1`.

`--explain-retrieval` shows the local strategy, derived terms, triggered concepts, candidate count, selected source names, native locators, scores, and FTS ranks. Machine-readable ask and task evidence records the same versioned decision in `retrieval.json` and the terminal result contract.

Detailed retrieval explanations are local operator evidence. Sanitized field-trial summaries exclude source names, paths, filenames, locators, questions, excerpts, raw queries, prompts, model output, and source or segment identifiers.

## Citation envelope

Every result carries:

- canonical object SHA-256;
- source name and media type;
- normalized kind;
- exact format-native locator such as line, page, paragraph, sheet/cell, or slide/shape;
- deterministic segment identifier and segment index;
- normalized-document path and SHA-256;
- exact text SHA-256.

Before a result enters a model prompt, report, or user-visible result, Archiv independently verifies that:

1. the canonical original exists and still matches its content address;
2. the citation points to the canonical normalized path;
3. the normalized document exists and matches its recorded hash;
4. object, source, media type, and kind still agree;
5. the indexed segment index, locator, text hash, and deterministic identifier still agree.

A stale or fabricated citation is rejected rather than returned as a partial result.

The Python APIs are:

```python
from archiv.search import (
    read_source_excerpt,
    retrieve_evidence,
    search_documents,
    validate_citation,
)

literal_results = search_documents("exact phrase")
evidence = retrieve_evidence("What remains unfinished?", evidence_limit=8)
validation = validate_citation(evidence.results[0].citation)
excerpt = read_source_excerpt(evidence.results[0].citation)
```

## Bounded source location

Use one explicit object digest or citation to locate the preserved original after independent revalidation:

```bash
archiv source 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
archiv find "quarterly finding" --json > matches.json
archiv source --citation-file matches.json --citation-number 1
```

`--citation-file` accepts a raw citation object, one search result, a list of find results, an ask result containing `retrieved_citations`, or a report manifest containing `sources`. `--citation-number` is one-based and prevents ambiguous implicit selection.

The command revalidates the citation envelope, canonical original hash, normalized source metadata, and the resolved path boundary. It rejects malformed or uppercase digests, stale or fabricated citations, object substitution, symbolic links, and any path that resolves outside Archiv-controlled storage. Output is read-only metadata; the command does not launch an application, execute document content, follow external links, or expose a general file browser.

## Current boundary

TXT/Markdown, PDF, DOCX, XLSX, and PPTX normalized text is indexed with exact locations. Images and audio are represented in durable normalized metadata, but their text is not fabricated: image OCR and audio transcription remain unavailable until real local processors are integrated. The citation model is locator-agnostic and can carry image regions, audio timestamps, or metadata-chunk locations when those processors produce evidence.

OpenDocument and native InPage ingestion are tracked separately in issue #38. Bounded source location is implemented by `archiv source`; opening, executing, editing, or browsing arbitrary files remains outside the trust boundary.

## Why SQLite first

SQLite FTS5 is sufficient for the current single-user corpus, ships inside the same Python runtime, and adds no daemon, account, network dependency, or second authoritative database. The frozen 22-question benchmark reached full required-source recall at evidence limit 8 without adding Qdrant, Elasticsearch, PostgreSQL, graph stores, or embeddings. Any such expansion requires new benchmark evidence.
