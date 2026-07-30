# Local full-text retrieval and citations

Archiv's first retrieval layer uses SQLite FTS5 over the normalized segments produced by immutable ingestion. It is intentionally small, local, and completely rebuildable.

## Build and search

```bash
archiv rebuild-search-index
archiv search "ARCHIV-DOCX-MARKER-2026"
archiv search "quarterly finding" --source-name report.pdf --kind pdf
```

The index is stored at `ARCHIV_HOME/indexes/search.sqlite3`. Deleting it does not delete originals, normalized documents, metadata, or provenance. Re-run `archiv rebuild-search-index` to recreate it atomically.

Search treats the supplied text as a literal phrase rather than exposing raw FTS query syntax. Optional filters match exact source name, media type, normalized kind, or object SHA-256.

## Citation envelope

Every result carries:

- canonical object SHA-256;
- source name and media type;
- normalized kind;
- exact format-native locator such as line, page, paragraph, sheet/cell, or slide/shape;
- deterministic segment identifier and segment index;
- normalized-document path and SHA-256;
- exact text SHA-256.

Before a result is returned, Archiv independently verifies that:

1. the canonical original exists and still matches its content address;
2. the citation points to the canonical normalized path;
3. the normalized document exists and matches its recorded hash;
4. object, source, media type, and kind still agree;
5. the indexed segment index, locator, text hash, and deterministic identifier still agree.

A stale or fabricated citation is rejected rather than returned as a partial result.

The Python APIs are:

```python
from archiv.search import read_source_excerpt, search_documents, validate_citation

results = search_documents("exact phrase")
validation = validate_citation(results[0].citation)
excerpt = read_source_excerpt(results[0].citation)
```

## Current boundary

TXT/Markdown, PDF, DOCX, XLSX, and PPTX normalized text is indexed with exact locations. Images and audio are represented in durable normalized metadata, but their text is not fabricated: image OCR and audio transcription remain unavailable until real local processors are integrated. The citation model is locator-agnostic and can carry image regions, audio timestamps, or metadata-chunk locations when those processors produce evidence.

## Why SQLite first

SQLite FTS5 is sufficient for the current single-user corpus, ships inside the same Python runtime, and adds no daemon, account, network dependency, or second authoritative database. Qdrant, Elasticsearch, PostgreSQL, graph stores, or embeddings require benchmark evidence before adoption.
