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
archiv ingest ./document.docx --home /srv/archiv
archiv rebuild-derived <sha256>
```

`archiv ingest` validates the claimed format before creating the archive. Unsupported and malformed inputs exit non-zero and are not copied into canonical storage.

`archiv rebuild-derived` deletes only the selected object's derived directory and recreates it from the immutable original. It verifies the original digest before returning success.

## Initial format surface

The first ingestion slice supports UTF-8 TXT/Markdown, PDF, DOCX, XLSX, PPTX, PNG/JPEG and WAV. It creates a portable normalized JSON document with format-native locators such as page, paragraph, sheet/cell and slide/shape.

OCR and transcription are not silently simulated. Image and audio ingestion create explicit `not_run` status artifacts until real local processors are integrated.

## Trust rules

- A matching digest reuses one canonical original and records another ingestion event.
- Derived files may be deleted at any time and rebuilt.
- Search indexes are outside the canonical store.
- Processing success is recorded per processor with output path and SHA-256 evidence.
- The source and stored-original hashes are checked after processing.
- A process exit without these checks is not acceptance evidence.
