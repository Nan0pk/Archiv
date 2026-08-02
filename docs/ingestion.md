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
archiv ingest ./document.odt
archiv ingest ./template.ott
archiv ingest ./document.fodt
archiv ingest ./equation.odf
archiv ingest ./document.docx --home /srv/archiv
archiv rebuild-derived <sha256>
```

`archiv ingest` validates the claimed format before creating the archive. Unsupported and malformed inputs exit non-zero and are not copied into canonical storage.

`archiv rebuild-derived` deletes only the selected object's derived directory and recreates it from the immutable original. It verifies the original digest before returning success.

## Current format surface

Archiv supports UTF-8 TXT/Markdown, PDF, DOCX, XLSX, PPTX, PNG/JPEG, WAV and the following bounded OpenDocument representations:

- package documents: ODT, ODS, ODP and ODG;
- package templates and master documents: OTT, ODM, OTM, OTS, OTP and OTG;
- single-file flat XML: FODT, FODS, FODP and FODG;
- packaged MathML formula documents: ODF.

The normalized JSON uses format-native locators such as page, paragraph, sheet/cell, slide/object, drawing page/object and formula/MathML representation.

ODF ingestion is deliberately bounded and non-executing. Package documents validate the exact registered mimetype, required manifest declarations, expected content root and body, safe archive paths, supported compression, XML structure and aggregate repeat expansion. Flat XML documents validate the `office:document` root, exact internal `office:mimetype`, body subtype and XML limits. Spreadsheet formulas are recorded but never executed. Formula documents preserve a bounded MathML source representation and searchable formula text without evaluation. External links are counted and ignored. ODF text-space, tab and line-break elements are preserved in normalized text.

The current ODF claim does **not** include ODB databases, charts/images as standalone ODF documents, presentation notes or native InPage files. Native `.inp` support remains unclaimed until lawful real fixtures and independently verified Urdu/Arabic extraction are available. Unsupported formats fail explicitly rather than falling back to cloud conversion or OCR-equivalence claims.

OCR and transcription are not silently simulated. Image and audio ingestion create explicit `not_run` status artifacts until real local processors are integrated.

## Trust rules

- A matching digest reuses one canonical original and records another ingestion event.
- Derived files may be deleted at any time and rebuilt.
- Search indexes are outside the canonical store.
- Processing success is recorded per processor with output path and SHA-256 evidence.
- The source and stored-original hashes are checked after processing.
- A process exit without these checks is not acceptance evidence.
