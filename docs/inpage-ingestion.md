# Native InPage ingestion

Archiv ingests native InPage `.inp` documents locally and makes their text available to `archiv find`, `archiv ask`, reports, MCP tools and any other capability built on Archiv's normalized segment index.

## What happens on ingest

```text
.inp file
→ bounded CFB validation
→ exact root InPage100 or InPage300 stream selection
→ local text extraction
→ normalized segments with stream/byte-offset locators
→ immutable original plus rebuildable extracted text
→ normal Archiv full-text index and citations
```

The source file is never uploaded or opened by the InPage application. Archiv stores its exact SHA-256-addressed bytes read-only and can rebuild improved derived text later from that original.

## Supported native streams

### InPage300

Archiv reads bounded UTF-16LE text runs from the native `InPage300` content stream. It keeps Arabic/Perso-Arabic and ordinary printable text, rejects malformed code units, splits bounded searchable segments, and removes exact duplicate segments.

### InPage100

Archiv scans aligned 16-bit units in the native `InPage100` stream. A unit is treated as legacy text only when its high byte is `0x04`; formatting/control units are not reinterpreted as Urdu. Legacy low-byte values are converted through the built-in first-key mapping. Unmapped values are skipped and reported instead of being guessed.

## Deliberate boundary

This release solves document preservation and searchable textual use. It does not reconstruct the visual page.

Normalized metadata therefore states:

```json
{
  "native_text_extracted": true,
  "text_fidelity": "searchable_best_effort",
  "layout_supported": false
}
```

Text follows native content-stream order. Page geometry, text frames, columns, stories, styles and exact rendering remain outside the claim. Those limitations do not prevent search, evidence retrieval, grounded question answering or rebuilding extraction later from the canonical original.

## Safety and limits

Archiv fails closed when the file is not a valid bounded CFB document, the required `DocumentInfo` stream is missing, the native content stream is absent or ambiguous, the stream variant is unknown, configured size/sector/segment limits are exceeded, or no safely extractable text is found.

No online converter, network request, external binary, macro, script or installed copy of InPage is used.

## Provenance and attribution

The InPage100 high-byte framing rule and legacy first-key character values were implemented from the publicly inspectable `ShakesVision/html-experiments` repository at commit `1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a`, principally:

- `master_inpage.md`;
- `inpage/shared/inp-record-parser.js`;
- `inpage/pdf-texter/UnicodeToInpage/InpageToUni.xml`.

Original work: Shakeeb Ahmad, https://shakeeb.in. Archiv's implementation is a changed, independent Python implementation integrated with Archiv's bounded CFB reader, immutable storage, normalized-document contract and citation pipeline. The upstream attribution-required licence is retained in `third_party/ShakesVision-html-experiments-LICENSE.txt`.
