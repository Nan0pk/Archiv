# Architecture

## Boundary

Archiv is the durable knowledge, capability, provenance, and validation core. Human-facing workbenches such as CoWork-OS are adapters, not owners of Archiv state.

```text
operator surface
      |
      | CLI or MCP
      v
Archiv request boundary
      |
      +-- policy resolution
      +-- direct or model-assisted executor
      +-- task-specific capabilities
      +-- independent validators
      +-- append-only run evidence
      |
      v
canonical originals + structured metadata + rebuildable indexes
```

## Durable layers

### Canonical originals

Original bytes are content-addressed, hashed, and never silently overwritten.

### Normalized representations

Extracted text, OCR, transcripts, tables, previews, and structural metadata are derived products that can be rebuilt.

### Structured metadata

SQLite initially stores file identity, hashes, dates, relationships, processing state, and provenance.

### Search

SQLite FTS5 is the first search engine. Embeddings may be added later as another rebuildable index only after benchmark evidence.

### Capabilities

A capability has a versioned input schema, output schema, allowed reads, allowed writes, forbidden paths, timeout policy, network policy, evidence output, and validator.

### Executor

Direct deterministic operations do not require model planning. Model assistance is introduced only when interpretation or drafting is needed. Planned workflows are reserved for genuinely dependent multi-step tasks.

### Validation

Validators live outside the model call and reject missing artifacts, changed sources, malformed Office files, incorrect calculations, unresolved citations, and false completion claims.

## Initial technology choice

Python 3.12 keeps the first core compact and supports document processing, SQLite, Office generation, validation, CLI, and MCP without introducing a second application runtime.
