# Release notes

## Unreleased

Nothing yet.

## 0.1.0a7 (release candidate, 2026-11-18)

An alpha release candidate. Beta readiness still needs the owner-run trials and the
two-person privacy review in `docs/definition-of-done.md`. Audio, video and the face steps
(S19 to S27) are deferred to after this release (`docs/plan/DECISIONS.md`, decision 13).

### Upgrading from 0.1.0a6

- Open an existing archive in place: the first command that writes to it (for example
  `archiv add`) brings its database up to date, and older documents stay searchable. Checked
  on an archive made by the 0.1.0a6 commit from its own generated test files, one of each
  format it read (Word, Excel, PowerPoint, PDF, plain text, InPage and fourteen OpenDocument
  variants): the same 20 files matched the same search before and after the upgrade. Until
  that first write, `archiv status` shows 0 documents and a missing-table warning (see
  `docs/known-issues.md`).
- To move an archive to another folder, use `archiv backup` and `archiv restore`; a folder
  copied by hand cannot be searched (see `docs/known-issues.md`).
- The Fedora installer downloads a full 40-character commit SHA given as `--ref` directly,
  without asking GitHub's API first; a branch or tag name is still looked up there, and
  without a token that lookup can meet GitHub's limit of 60 requests an hour.
- Text derived from some PDFs can change after `rebuild-derived`, because pypdf 7 reads a few
  pages differently; saved answers citing those pages may then stop validating.

### Sandboxing

- OCR now runs in the same stricter sandbox as the LibreOffice conversion: it can no longer
  reach the user's session bus or see files under `/home`, `/mnt` or `/media`, and gets only
  a minimal environment. SVG rendering (`resvg`), which had no sandbox at all, now runs
  inside it too.

### Institutional memory (`archiv memory`, new)

- `archiv memory build` makes a web of the things a collection names (repeated phrases,
  abbreviations the text defines, optional meaning vectors) without any model;
  `archiv memory ask` answers a question with a cited report, handing text to a model
  command that you name (`--model-cmd`) at the question's first and last steps; where that
  command sends the text is up to you (see the README's privacy section). The design and its measurements are in
  `docs/institutional-memory-design.md`.
- A question searches its own words directly, follows the web one step out from its starting
  names by default, says when its answer had less to work with, and records the hash of the
  embedding weights with the vectors. Embedding model files are looked for in
  `~/.cache/archiv/fastembed` first; Archiv never downloads one.

### Document readers

- Word: tables, headers, footers, notes, comments and link targets (S15). Excel: stored
  formula results, links, merged ranges, hidden marks and charts, with processing limits
  across library calls and cells outside wrong sheet dimensions recovered (S16 to S16C).
  PowerPoint: tables, charts, grouped shapes, speaker notes and hidden marks (S17).
  OpenDocument: tables, speaker notes, links, notes and structural state (S18, S18A).
- XML declarations are refused whatever the encoding (S16A).
- Office lock files (`~$name.docx`) are left out of folder adds.
- Damaged old `.ppt` files are read through sandboxed LibreOffice (below).

### Models and evidence

- A model outside this machine is refused unless the archive is marked as an evaluation
  archive; every answer and report says where its model ran, records every model call, and
  refuses a paid request the archive has not budgeted for (S02 to S09).

### Speed and memory on large collections

- Folder adds start the biggest files first, give large files their own process, hold files
  back when memory is short, batch their database writes, and fill the search index a
  document at a time. Text-heavy PDFs are no longer held in memory whole. The entity graph
  is rebuilt in a working file only when asked for, instead of in memory on every add.

### User-facing

- Old `.ppt` files our own reader refuses as damaged (a record longer than the record holding
  it) are now read through LibreOffice inside a bubblewrap sandbox with no network; their text is
  labelled `origin: libreoffice_conversion`. The Fedora installer adds `libreoffice-impress`.
- A memory question follows the web one step out from its starting names by default (faster;
  same exact-word and other-form passages on the measuring kit).
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
  - EXIF, IPTC, and XMP metadata extraction (captions, authors, dates). GPS coordinates, when an
    image has them, are kept in its derived, deletable metadata and never in a diagnostics export.
  - Archive recursion (ZIP, TAR) unpacking members as first-class content-addressed objects with `containment` hierarchy tracking.
  - PDF layout-aware reading order, table extraction into `NormalizedTable`, and embedded attachment ingestion.
  - A PDF page with fewer than 25 characters of native text is read with local OCR, when
    Tesseract is installed.
  - An image index (`indexes/images.sqlite3`) of colour-and-edge features for
    `archiv images find-similar`. There is no search over images by description (the old one
    was a nineteen-entry colour lookup table; see the breaking change below), and
    `archiv images duplicates` refuses to group duplicates until an evidence-backed
    replacement exists.
  - Face analysis is off until `archiv faces opt-in`, and `archiv faces forget` erases its
    data. Automatic face detection is switched off: it finds no faces until a real detector
    replaces the old skin-colour heuristic (step S24, deferred), so `archiv faces scan` adds
    nothing new. Names are never attached to a face automatically.
  - A rebuildable entity graph (`indexes/graph.sqlite3`, `archiv graph`) built from text
    matches and file metadata. A capitalised two-word phrase is a `candidate_mention`, not a
    confirmed person, and no unmeasured confidence number is shown (step S12).

### Breaking changes

- **`archiv images search <words>` is gone.** It has become `archiv images find-similar
  --image PATH`, which takes a path to an image and finds images that look like it. The
  removed command searched by text description, and the thing answering those queries was
  a nineteen-entry colour lookup table with a hash-scatter fallback, so every string
  produced confident-looking ranked results: in a corpus with no people in it, "a photo of
  a person smiling" ranked a document first. A script calling the old command will now
  fail rather than return nonsense. See `docs/plan/steps/S10.md` for the measurements.

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
