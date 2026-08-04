# Local visual OCR

Archiv can recover visible text from PNG/JPEG images and from PDF pages that contain no native text. OCR output is a replaceable derived interpretation; it never overwrites the immutable original or silently becomes native extraction.

## Processing order

```text
immutable original
├── native format normalizer
└── visual OCR when applicable
    ├── source image, or
    └── image-only PDF page rendered by pdftoppm
        └── Tesseract TSV
            └── normalized visual_ocr spans
```

For PDFs, the first implementation OCRs only pages whose native extraction is empty. A PDF page with native text is not reinterpreted automatically.

Each OCR span records:

- `origin: visual_ocr`;
- page and line number;
- pixel bounding box;
- OCR engine and selected languages;
- confidence when Tesseract reports it.

The normal search index, citations, reports and MCP tools consume those attributed spans through the existing normalized-document contract.

## Derived evidence

```text
ARCHIV_HOME/derived/<source-sha256>/
├── normalized/document.json
├── extracted/text.txt
├── ocr/
│   ├── status.json
│   └── page-0001.tsv
└── previews/pages/page-0001.png   # image-only PDF pages only
```

`ocr/status.json` is deterministic processor evidence. It records the source hash, engine and renderer versions, executable hashes, selected and available languages, page-image hashes, raw-output hashes, limits, warnings and completion state. Machine timestamps remain in Archiv's processing ledger rather than the deterministic manifest.

## Local requirements

Tesseract is optional. `pdftoppm` from Poppler is required only for image-only PDF pages. On Fedora:

```bash
sudo dnf install \
  tesseract \
  tesseract-langpack-eng \
  tesseract-langpack-ara \
  tesseract-langpack-urd \
  poppler-utils \
  bubblewrap
```

The contributed `urd_naw` Nastaliq model is not assumed to be installed or production-proven. Archiv uses it automatically when present, unless languages are selected explicitly.

## Configuration

```bash
# Use installed English, Urdu and Arabic models.
export ARCHIV_OCR_LANGUAGES=eng+urd+ara

# Prefer a separately installed contributed Nastaliq model.
export ARCHIV_OCR_LANGUAGES=eng+urd_naw+ara

# Require Linux bubblewrap network isolation; fail the OCR processor if unavailable.
export ARCHIV_OCR_SANDBOX=required

# Disable OCR without disabling image/PDF ingestion.
export ARCHIV_OCR=off
```

`ARCHIV_OCR_SANDBOX` accepts:

- `auto` (default): use bubblewrap when it is installed; otherwise invoke the fixed local processor directly and record `sandbox: none` in page evidence;
- `required`: require bubblewrap network isolation and fail the OCR processor if it cannot be established;
- `off`: invoke the configured local executable directly. This is intended for controlled tests and platforms without bubblewrap.

If bubblewrap is selected and the sandboxed processor fails, Archiv records a failed OCR result rather than silently retrying outside the sandbox.

Missing executables or requested language models produce a `skipped` manifest. Processor crashes, timeouts or invalid output produce a `failed` manifest. Neither state fabricates text or invalidates an otherwise valid immutable ingestion.

## Bounded policy

The first implementation enforces:

- 200 MiB maximum source/image size for OCR processing;
- 80 million maximum pixels per image or rendered page;
- 250 maximum PDF pages for OCR fallback;
- 60 seconds per page for rendering and recognition;
- 200 DPI PDF rendering;
- two OpenMP worker threads unless the environment already provides a limit.

These are safety limits, not quality or performance claims. OCR accuracy, reading order and resource cost must be measured against lawful Archiv-specific fixtures before any engine or language model is declared the production default.

## Trust boundary

- Native text and visual OCR remain separate segments with separate locators.
- OCR text is untrusted document content and cannot invoke tools or alter processing policy.
- Raw TSV and rendered pages remain inspectable derived evidence.
- OCR may be deleted and rebuilt without re-importing the source.
- Tesseract confidence is not proof that a reading is correct.
- OCR does not reconstruct hidden metadata, document semantics, original fonts, native frames or proprietary layout structures.

Native InPage page geometry and styles remain tracked separately in issue #53. Visual OCR can recover or verify visible content but is not native InPage parsing.
