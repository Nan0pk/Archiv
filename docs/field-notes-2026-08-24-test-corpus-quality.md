# Test-corpus quality notes — companion to the Archiv field evaluation

> **Status: nascent / informal.** Companion to
> [`field-notes-2026-08-24-real-world-corpus-retest.md`](field-notes-2026-08-24-real-world-corpus-retest.md).
> Assesses the *test corpus*, not the tool.

**Corpus:** 2,097 files, 498 MB, plus a 22-question grounding manifest (`benchmark_manifest.json`, v2.0.0)
**Assessed against:** Archiv `0.1.0a6` @ `a31bd0f`
**Purpose:** separate defects in the *tool* from defects in the *test data*, so the retrieval
numbers in the main report can be read correctly.

All content is redacted: this corpus contains real personal and security-sensitive records
(personnel rosters, payroll, incident reporting). No document text, place name, personal name,
or filename appears below.

---

## 1. Why this matters

The main report scores 11 of 20 manifest questions as retrieving their expected source. Taken at
face value that reads as a 55% retrieval rate for the tool. It is not. Of the nine misses,
**four are ranking behaviour, two are OCR quality on genuinely hard scans, one is a real tool
bug, and two are questions this corpus cannot answer as written.**

**Important correction, established by controlled experiment after the first draft of these
notes.** An earlier reading — shared by the previous evaluation and by the upstream field
notes — held that the corpus's duplication was the dominant cause of the retrieval misses, and
that repairing the corpus should therefore come first. That was tested by re-ingesting the
corpus with the duplicate digest removed and re-running the identical benchmark. **The score did
not move: 11/20 both ways, zero misses converted to hits.** The corpus defects below are real and
worth fixing, but they are not what is holding the score down — a missing per-source diversity
cap in `find`/`search` is (see [the main report](field-notes-2026-08-24-real-world-corpus-retest.md), §2.5). Fix the tool and the corpus independently;
neither blocks the other.

## 2. The single dominant defect: one file is a third of the index

| Metric | Value |
|---|---|
| Total indexed segments | 441,809 |
| Segments from one concatenated digest file | **148,190** |
| Share of the entire index | **33.5%** |
| Source documents concatenated into it | 1,867 |
| File size | 23.9 MB plain text |

This file is a flat concatenation of the same daily reports that are *also* present in the
corpus individually, each preceded by a header giving its original path. Every one of its
1,867 constituent documents is therefore indexed twice — once as its own DOCX, once inside the
digest.

**Measured impact of removing it** (separate archive, hardlink mirror, source corpus untouched):

| Metric | With digest | Without |
|---|---|---|
| Index segments | 441,809 | 293,619 (−33.5%) |
| Benchmark score | 11 / 20 | **11 / 20 — unchanged** |
| Ranks improved / worsened | — | 4 / 0 (best: rank 9 → 4) |
| `find`, common term | 4.4–4.6 s | 3.4–3.6 s (−23%) |

So the digest costs a third of the index, roughly a quarter of query latency, and a few rank
positions — but it is **not** what causes the retrieval misses. It is worth removing for
efficiency and measurement hygiene, not because it will improve the score.

**Recommendation:** do not delete it. Move it outside the scored set and label it explicitly as a
duplication stress-test fixture — it is a legitimate real-world artefact, it exercises a genuine
ingestion path, and it was useful in diagnosing the ranking behaviour. A `mv` is reversible; a
`rm` is not.

Segment concentration more broadly — the five largest sources supply 60% of the index:

| Rank | Kind of source | Segments |
|---|---|---|
| 1 | Concatenated digest (above) | 148,190 |
| 2 | Payroll spreadsheet | 36,696 |
| 3 | Facilities spreadsheet | 27,517 |
| 4 | Payroll spreadsheet | 27,188 |
| 5 | Facilities spreadsheet | 25,427 |

The spreadsheet counts are expected rather than defective — Archiv segments spreadsheets per
cell by design — but they are worth knowing when interpreting rankings, and they interact with
the cross-cell phrase limitation noted in the main report.

## 3. Manifest questions that cannot pass as written

Two of the 20 scored questions are unanswerable regardless of tool quality:

| Question | Expected source | Problem |
|---|---|---|
| Legacy incident report | a `.doc` file | `.doc` is on Archiv's **explicit rejection list**. The tool refuses it up front, by design and documented. The manifest asks for a citation from a file the tool will never ingest. |
| Audio recording metadata | a `.wav` file | Audio is catalogued **metadata-only, no document text**, by design. There is no text segment to cite, and the required facts are the filename and the MIME type — neither of which is FTS-indexed content. |

Both should either be removed, re-pointed at a supported source, or reclassified as
negative/refusal tests with the expectation inverted.

## 4. Manifest facts that do not appear in their own source

Three further questions specify `required_facts` that are absent from the expected source's
extracted text. Verified per fact with `search <fact> --source-name <expected source>`:

| Question | Diagnosis |
|---|---|
| Election security plan | **Tool bug, not test data.** The expected source is a scanner-watermarked PDF; Archiv skipped OCR because the watermark counts as a native text layer, so only 3 segments (10 characters/page) were extracted. See finding 3.1 in the main report. Fix the tool and this question becomes answerable. |
| Tabular refugee data | The source OCR'd successfully (133 segments), but the manifest's required facts do not appear in the OCR output. The facts read as if they were taken from the **filename** rather than the page content — and filenames are not indexed content. |
| Census table image | OCR of a photographed tabular sheet produced largely unusable output (13 segments, fragmentary RTL characters). Genuinely hard input; the expectation is unrealistic as written. |

**Recommendation:** derive `required_facts` mechanically from each source's *extracted text*
rather than from filenames or from reading the original by eye. A manifest fact that the
extractor cannot produce tests nothing.

## 5. Structural issues in the file set

| Issue | Count | Detail |
|---|---|---|
| Zero-byte files | 6 | 1 deliberate edge-case PDF; 5 JPEGs in an image folder that appear unintentional — they carry real-looking filenames in a sequence alongside valid images |
| Word lock file | 1 | A `~$…docx` temp artefact committed alongside the real documents |
| Content-duplicate files | 10 | 2,097 files, 2,087 distinct SHA-256. Six share the empty-file hash; the rest are four genuine pairs, one of which is a same-name `(2)` copy |
| Unsupported extensions | 26 | `.db` ×10, `.doc` ×4, `.m4a` ×2, `.opus` ×2, `.rtf` ×2, and one each of `.json`, `.mp3`, `.accdb`, `.kml`, `.sqlitedb`, `.chm` |

The unsupported set looks deliberate and is useful — it exercises up-front rejection, and every
one was rejected correctly. The zero-byte JPEGs and the Word lock file look accidental and
should be removed; they currently inflate the "skipped" count and obscure the deliberate edge
cases.

## 6. What the corpus does well

Worth stating plainly, because the list above is all defects:

- **Format breadth is genuinely valuable.** DOCX, native and scanned PDF, XLSX, PPTX, the full
  ODF family including `.odb` and `.odf`, InPage `.inp`, JPEG/PNG photographs, and WAV — this
  exercised nearly every branch of the ingestion matrix, and it surfaced a real high-severity
  bug that a synthetic corpus would not have.
- **RTL coverage is a real strength.** Urdu, Pashto, Dari and Arabic content in several
  containers, including native InPage extraction. All four RTL questions retrieved their
  expected source, several at rank 1–4.
- **The deliberate malformed set works.** Corrupted zip, decompression-expansion, and zero-byte
  files each triggered the correct fail-closed path with a distinct error.
- **The anti-hallucination probes are well constructed.** Both false-premise questions are
  plausible enough to tempt a small model while being unambiguously absent from the corpus —
  which is exactly why they caught a live fabrication.
- **Scale is realistic.** 2,097 files across nested real-world folder structures with irregular
  naming, inconsistent spacing, and duplicated stems is a far better ingestion test than a flat
  synthetic tree.

## 7. Recommended repairs, in priority order

1. **Regenerate `required_facts` from extracted text** rather than from filenames — three
   questions currently specify facts their own source does not contain (§4). Highest-value
   change: it is the only corpus repair that can move the score.
2. **Retire or re-point the two structurally unanswerable questions** (§3).
3. **Move the concatenated digest out of the scored set** — measured at −33.5% index size and
   −23% query latency, but no score change, so this is hygiene rather than a fix (§2).
4. **Delete the 5 zero-byte JPEGs and the Word lock file**; keep the deliberate edge cases in
   their own folder (§5).
5. **Re-score after 1–4** — and note that the ceiling is currently set by the tool, not the
   corpus: even a perfectly repaired corpus leaves the four diversity-cap misses in place until
   `find`/`search` gains a per-source cap.

*Corpus assessed 2026-08-24 against a clean archive home; per-question retrieval results and
per-source segment statistics retained locally.*
