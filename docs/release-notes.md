# Release notes

## Unreleased

### User-facing

- Updated the documented project maturity from pre-alpha to alpha without changing the
  independent security-review gate or operational warnings.
- Added a task-oriented desktop application for library setup, ingestion, search, grounded
  questions, sources, reports, recovery, and settings.
- Retained the command-oriented desktop console for maintainers behind `archiv ui --diagnostic`.
- Added user-invoked CLI and desktop diagnostics export with a full pre-save preview.
- Added versioned support-bundle schema metadata, aggregate operational/validation status,
  sanitized error categories, and dependency/platform compatibility facts.
- Added privacy regression coverage and support guidance that avoids requesting archives.
- Documented redaction guarantees, residual risks, compatibility, and known issues.
- Added multimedia and format capability expansion:
  - Extensible `Extractor` registry with content-signature sniffing before file suffix checks.
  - Native raster image support (GIF, BMP, TIFF, WEBP) and hardened SVG XML parsing.
  - EXIF, IPTC, and XMP metadata extraction (captions, authors, dates) with privacy-protected GPS handling.
  - Archive recursion (ZIP, TAR) unpacking members as first-class content-addressed objects with `containment` hierarchy tracking.
  - PDF layout-aware reading order, table extraction into `NormalizedTable`, and embedded attachment ingestion.
  - OCR text-density heuristics to recover watermarked and scanned documents.
  - Image embeddings, semantic search, and near-duplicate detection in `indexes/images.sqlite3`.
  - Privacy-first (GDPR Art. 9 / BIPA) opt-in face clustering, evidence-cited candidate name attribution (`archiv who`, `archiv faces`), and first-class erasure.
  - Rebuildable evidence-backed entity graph (`indexes/graph.sqlite3`, `archiv graph`) enabling multi-hop cross-corpus queries and 360° entity profiles.

### Reliability

- Added transactional storage schema migrations, pre-migration recovery snapshots, and shared
  database, object, and JSON-evidence integrity checks across operational workflows.
- Added fail-closed ingestion resource ceilings for input size, archives, documents, images, and
  subprocess CPU, memory, concurrency, and wall time.

### Security

- Documented the ingestion and supply-chain threat model, adversarial verification requirements,
  dependency maintenance policy, and independent-review gate.
- Added a versioned private beta trial protocol with consent, local-only execution, fixed corpus
  and hardware strata, recovery exercises, evidence thresholds, and privacy-safe aggregates.
