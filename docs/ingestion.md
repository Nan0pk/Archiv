# Immutable ingestion

Archiv treats original bytes as canonical evidence and every extraction product as rebuildable.

## Storage location

The storage root is resolved in this order:

1. `--home` supplied to the CLI;
2. `ARCHIV_HOME`;
3. `$XDG_DATA_HOME/archiv`;
4. `~/.local/share/archiv`.

The default never uses the current repository or working directory.

```text
ARCHIV_HOME/
├── originals/sha256/<prefix>/<digest>
├── derived/<digest>/
│   ├── normalized/document.json
│   ├── extracted/text.txt
│   ├── ocr/status.json
│   ├── transcripts/status.json
│   ├── tables/tables.json
│   └── previews/metadata.json
├── indexes/
├── temporary/
└── archiv.sqlite3
```

Originals are stored without an extension under their SHA-256 digest and made read-only. The database retains media type, source extension, source names and paths, every import attempt, duplicate status, and every processor outcome.

## Commands

```bash
archiv ingest ./document.docx
archiv ingest ./urdu-document.inp
archiv ingest ./document.odt
archiv ingest ./template.ott
archiv ingest ./document.fodt
archiv ingest ./equation.odf
archiv ingest ./database.odb
archiv ingest ./document.docx --home /srv/archiv
archiv rebuild-derived <sha256>
```

`archiv ingest` validates the claimed format before creating the archive. Unsupported and malformed inputs exit non-zero and are not copied into canonical storage.

`archiv rebuild-derived` deletes only the selected object's derived directory and recreates it from the immutable original. It verifies the original digest before returning success.

## Current format surface

Archiv supports native InPage INP, UTF-8 TXT/Markdown, PDF, DOCX, XLSX, PPTX, PNG/JPEG, WAV and the following bounded OpenDocument representations:

- native InPage100 and InPage300 documents under the searchable-text policy below;
- package documents: ODT, ODS, ODP and ODG;
- package templates and master documents: OTT, ODM, OTM, OTS, OTP and OTG;
- single-file flat XML: FODT, FODS, FODP and FODG;
- packaged MathML formula documents: ODF;
- packaged database front ends: ODB, under the metadata-only policy below.

The normalized JSON uses format-native locators such as InPage content stream and byte offset, page, paragraph, sheet/cell, slide/object, drawing page/object, formula/MathML representation and database object type/name.

### Native InPage searchable-text policy

Archiv accepts `.inp` files as `application/x-inpage`, validates their CFB container locally, preserves the original bytes unchanged, and extracts text into the same normalized and full-text-search pipeline used by other documents.

- `InPage300` text is read from bounded UTF-16LE content-stream runs.
- `InPage100` text is read from the legacy high-byte `0x04` text representation with a built-in first-key Unicode mapping.
- Unknown legacy codes are skipped and counted rather than guessed.
- Malformed containers, missing or ambiguous native streams, unknown variants, and files without safely extractable text fail closed.
- No online converter, installed InPage process, macro, document script, or network service is invoked.

The product claim is **native searchable-text ingestion**, not visual reconstruction. Extracted text follows content-stream order. Page geometry, text frames, stories, columns, styles and pixel-identical layout are not reconstructed. Each normalized document reports `text_fidelity: searchable_best_effort`, `native_text_extracted: true`, `layout_supported: false`, extraction measurements and warnings. The immutable original remains available for later higher-fidelity rebuilding without re-importing the source.

Implementation provenance and attribution are recorded in [Native InPage ingestion](inpage-ingestion.md).

ODF ingestion is deliberately bounded and non-executing. Package documents validate the exact registered mimetype, required manifest declarations, expected content root and body, safe archive paths, supported compression, XML structure and aggregate repeat expansion. Flat XML documents validate the `office:document` root, exact internal `office:mimetype`, body subtype and XML limits. Spreadsheet formulas are recorded but never executed. Formula documents preserve a bounded MathML source representation and searchable formula text without evaluation. External links are counted and ignored. ODF text-space, tab and line-break elements are preserved in normalized text.

### ODB metadata-only policy

Archiv treats an ODB file as a database front-end package, not as permission to open or run a database. It validates the exact `application/vnd.oasis.opendocument.base` package and `office:database` body, then exposes only bounded names and counts for tables, queries, forms and reports.

Connection descriptors, usernames, connection URLs and query commands are counted but never retained in normalized output. Queries are never executed. Embedded database members remain opaque: Archiv records only their aggregate count and byte size, never their paths or contents. Linked or embedded form/report documents are not parsed. No database driver, LibreOffice process, SQL engine, macro, script or network connection is invoked.

The current claim does **not** include extracting table rows, query results, database schemas beyond declared object names, form/report contents or protected/encrypted ODB packages.

OCR and transcription are not silently simulated. Image and audio ingestion create explicit `not_run` status artifacts until real local processors are integrated.

## Trust rules

- A matching digest reuses one canonical original and records another ingestion event.
- Derived files may be deleted at any time and rebuilt.
- Search indexes are outside the canonical store.
- Processing success is recorded per processor with output path and SHA-256 evidence.
- The source and stored-original hashes are checked after processing.
- A process exit without these checks is not acceptance evidence.
