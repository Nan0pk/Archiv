# Native InPage evidence and acceptance gate

Status: evidence gate, reviewed 2026-08-02

## Current decision

Archiv does not support native InPage `.inp` files yet. The suffix remains outside the format registry and must continue to fail explicitly.

Native support cannot be inferred from accepting a PDF/TXT export, converting pasted legacy text, rendering pages for OCR, or launching InPage itself. Support means validating a native file by content, preserving its original bytes, extracting its native text locally, and proving reading order and useful page/object locators against lawful ground truth.

## What the evidence supports

### Legacy container evidence

Public technical analysis of vulnerable InPage documents reports that InPage uses Microsoft structured storage and opens an `InPage100` stream. Community reverse-engineering also reports legacy files containing `DocumentInfo` and `InPage100` streams.

This supports treating Microsoft Compound File Binary Format as a candidate legacy container signature, not as an InPage signature by itself. The CFB header is a general-purpose container used by many unrelated formats. A parser must therefore validate InPage-specific streams and a supported internal version before accepting a file.

### Parser risk

CVE-2017-12824 records that a specially crafted InPage document can trigger arbitrary code execution. Detailed public analysis attributes the defect to insufficient validation while processing type fields in the `InPage100` stream.

Archiv must not use the legacy InPage application as an untrusted-file parser. Native extraction requires a bounded parser or a separately sandboxed, independently verified local adapter with strict resource, timeout and no-network controls.

### Existing conversion tools

The Center for Language Engineering publishes an InPage-to-Unicode converter page and user guide describing folder-based conversion of InPage files to Unicode text files. It is a candidate for future adapter evaluation, but this review did not independently inspect its downloadable binary, source, license text, supported InPage versions, command-line behavior or hostile-input safety.

Open-source InPage/Unicode converters provide useful glyph-mapping evidence but do not currently establish native file parsing. The inspected GPL application explicitly says it bypasses binary `.inp` parsing and converts pasted clipboard text. The inspected MIT package exposes text-to-text mapping APIs rather than a native document parser. Neither preserves native layout or satisfies native ingestion by itself.

## What remains unknown

No public, complete and authoritative native InPage format specification was found. The available evidence does not establish:

- signatures and internal structures for every supported InPage generation;
- reliable version detection;
- text-story, page and text-frame record layouts;
- encoding rules for every Urdu/Arabic/Perso-Arabic version and font map;
- protected, encrypted, image-only or partially corrupt document behavior;
- a lawful, redistributable native fixture corpus with independent ground truth;
- a local parser or converter whose license, privacy, determinism and hostile-input behavior have all been verified.

Offsets inferred from isolated samples, forum discussions or malware reports are not enough to implement a truthful general parser.

## Rejected shortcuts

The following do not qualify as native InPage support:

- trusting the `.inp` suffix;
- accepting any CFB/OLE container as InPage;
- uploading private documents to an online converter;
- treating a PDF or TXT export as the native source;
- treating OCR of a rendered page as native text extraction;
- converting only clipboard or pasted legacy glyph text;
- launching an old InPage build on untrusted input;
- deriving a production parser from exploit samples or pirated software;
- silently returning partial text when the version, encoding or layout is unknown.

## Required lawful fixture bundle

Each accepted native fixture must include all of the following:

| Evidence | Requirement |
|---|---|
| Native source | Original `.inp` file created by a licensed InPage installation |
| Permission | Explicit redistribution permission suitable for the repository and CI artifacts |
| Identity | SHA-256, byte size, creator version/build, operating system and creation date when known |
| Language | Declared Urdu, Arabic, Persian, Sindhi, Pashto/Pushto, Kashmiri and English content mix |
| Text truth | Unicode export or manually verified transcription produced from the same native document |
| Layout truth | Page count plus screenshots or PDF showing page order, text frames, stories and mixed-direction placement |
| Structure truth | Expected page/story/frame relationships where the source permits them |
| Negative cases | Corrupt, truncated, unrelated `.inp`, unsupported-version and protected/encrypted examples |
| Review record | Human verification of joining marks, bidi order, punctuation, numerals and Unicode normalization |

The initial fixture set must include at least Urdu-only, Arabic-only, mixed Urdu/English, multi-page, multiple text-frame, unsupported-version and corrupt cases.

## Direct-parser acceptance gate

A direct parser is acceptable only when it can demonstrate all of these properties:

1. Validate the container and InPage-specific structure before canonical storage.
2. Distinguish supported native InPage files from unrelated CFB/OLE and unrelated `.inp` formats.
3. Detect supported versions from content rather than filename.
4. Bound total file size, sectors, streams, records, recursion, text size and processing time.
5. Reject malformed chains, overlapping records, out-of-range types and truncated data without recovery guesses.
6. Extract native Unicode text without executing document content.
7. Preserve mixed-direction reading order and expose page, story, text-frame or object locators where proven.
8. Return explicit statuses for unsupported version, protected input, malformed input and image-only content.
9. Preserve the immutable original and produce deterministic, rebuildable derived output.
10. Pass full ingest, duplicate, rebuild, search, ask, report, backup, restore, MCP, privacy and offline tests.

## Local-adapter acceptance gate

A local converter or user-installed adapter is acceptable only if direct parsing is not technically or legally feasible and the adapter satisfies all of these conditions:

- source or binary identity, version and license are pinned and recorded;
- supported InPage versions and output semantics are documented;
- input and output remain local with network access denied;
- execution occurs with a temporary home, bounded CPU/memory/time and no access to Archiv originals beyond the selected read-only input;
- no shell interpolation or arbitrary output-path control is exposed;
- output is deterministic and independently checked against fixture ground truth;
- stdout, stderr, exit status and output hashes are recorded without leaking private text into public artifacts;
- unavailable, failed, timed-out and unsupported conversions remain explicit failures;
- the adapter is challenged with hostile and unrelated CFB/OLE inputs before release.

## Current external evidence blocker

The remaining issue is not blocked by a choice of agent or development environment. It is blocked by missing external evidence: lawful native fixtures with creator/version and layout ground truth, plus either sufficient format evidence for a bounded parser or an inspectable local converter whose license and behavior can be verified.

Until that evidence exists, Archiv's correct behavior is to reject `.inp` explicitly and make no native-support claim.

## Sources reviewed

- [Microsoft Compound File Binary Format specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/53989ce4-7b05-4f8d-829b-d08d6148375b)
- [Microsoft CFB header and identification signature](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea)
- [NVD record for CVE-2017-12824](https://nvd.nist.gov/vuln/detail/CVE-2017-12824)
- [QiAnXin analysis of the InPage100 vulnerability path](https://ti.qianxin.com/blog/articles/analysis-of-targeted-attack-against-pakistan-by-exploiting-inpage-vulnerability-and-related-apt-groups-english/)
- [Center for Language Engineering InPage-to-Unicode converter page](https://www.cle.org.pk/software/langproc/inpagetounicode.htm)
- [Center for Language Engineering converter user guide](https://www.cle.org.pk/Downloads/langproc/inpageunicode/Inpage-to-Unicode-Converter.pdf)
- [Community legacy-format reverse-engineering discussion](https://www.urduweb.org/mehfil/threads/%D8%A7%D9%86%D9%BE%DB%8C%D8%AC-%D9%81%D8%A7%D8%A6%D9%84-%D9%81%D8%A7%D8%B1%D9%85%DB%8C%D9%B9-%D9%BE%D8%B1-%D8%AA%D8%AD%D9%82%DB%8C%D9%82.26462/)
- [GPL paste-based InPage/Unicode converter](https://github.com/salmanasmat/InPageToUnicode)
- [MIT text-mapping converter package](https://packagist.org/packages/zanysoft/unicode-inpage-converter)
