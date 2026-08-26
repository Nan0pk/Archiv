# Archiv 0.1.0a6 — Second field evaluation on a real-world corpus

> **Status: nascent / informal.** A second ad hoc run against the same real-world corpus as
> [`field-notes-2026-08-23-real-world-corpus.md`](field-notes-2026-08-23-real-world-corpus.md),
> re-derived from a clean clone. Not the frozen, CI-measured benchmark in
> [`field-trial-report.md`](field-trial-report.md). Single machine, single pass, and a test
> corpus with its own quality issues (see the companion notes). Treat as a triage list, not a
> certified result.

**Tested:** `Nan0pk/Archiv` @ `a31bd0f` (main, one commit past the previous evaluation's `32a6a94`), fresh clone, `pip install -e '.[dev]'`
**Platform:** Fedora, Python 3.13.14 (uv venv), tesseract 5.5.3 (eng/ara/urd), bubblewrap present, LibreOffice present
**Corpus:** 2,097 real-world files, 498 MB — daily-report DOCX, native and scanned PDFs, XLSX, PPTX, the ODF family, InPage `.inp`, photographs including handwritten sheets, RTL text in Urdu/Pashto/Dari/Arabic, WAV audio, plus deliberately malformed and unsupported files
**Model backend:** Ollama loopback `http://127.0.0.1:11434` — `granite4:micro`
**`ARCHIV_HOME`:** on persistent disk (`/home`), deliberately *not* under `/tmp` — see §2.1

---

## 1. Executive summary

This is a re-test, from a clean clone, of the corpus evaluated previously at `32a6a94`. The
upstream `a31bd0f` commit is titled *"fix: bwrap /tmp masking in visual OCR, privacy-test
username leak"* and closes two of the six previously reported findings — one of them
successfully.

The system's core guarantees held up again at scale, and two of them are now measured more
strictly than before: backup/restore reproduces logical state exactly, and the search-index
rebuild is **byte-for-byte deterministic** (identical SHA-256 to the index the ingest itself
produced).

Material findings this round:

| # | Severity | Finding | Status vs. previous run |
|---|----------|---------|--------------------------|
| A | **High (bug)** | Scanner-watermark PDFs silently lose 100% of their content: a 10-character watermark counts as a "native text layer", so OCR is skipped. 6 of 20 valid PDFs affected. | **New** |
| B | **High (quality)** | `ask` still ships fabricated affirmative claims under a "Verified Sources" heading. Now reproduced in 2 of 5 runs on one probe. | Previously 3.2 — **still open** |
| C | **Medium (bug)** | The username literal is still in the repo — the fix commit's own documentation reintroduced it — and the guardrail meant to catch it is structurally blind in CI, so it passed. | Previously 3.3 — **still leaked, and the detector cannot see it** |
| D | **Medium (bug)** | An interrupted ingest leaves the archive in a state a plain re-run cannot heal, reported with a message that says the opposite of what is wrong. | **New** |
| E | Medium (usability) | `report` still fails wholesale on small local models; `--deterministic` is a working, undocumented escape hatch. | Previously 3.4 — **still open, severity reduced** |
| F | Medium (retrieval) | `find`/`search` apply no per-source diversity cap, so 3 spreadsheets can hold 98 of 100 result slots. Proven by controlled experiment to be the real cause — *not* the test corpus, contrary to the previous report. | Previously 3.5 — **still present, cause corrected** |
| G | Low (UX) | Batch `add` hides rejected-but-supported files inside an aggregate "unsupported" count and reports "0 failed". | Sharpens previous 3.6.4 |
| H | Low (UX) | `verify`, `restore` docs, and `--json` coverage — partial progress. | Previously 3.6 — **mixed** |

### A note on comparing numbers with the previous report

**The headline figures in this report are not comparable with the previous one.** Four things
changed at once: the corpus itself was modified after the previous run (2,097 files now vs.
2,098 then), Python differs (3.13.14 vs. 3.14.7), `ARCHIV_HOME` moved from a `/tmp` tmpfs to
persistent disk, and — because of that move — **OCR actually ran this time**, where previously
it was silently failing under `/tmp`. The segment count fell from 588k to 442k across those
changes; do not read that delta as a regression, it is not measuring the same thing. Only the
per-finding verdicts below are a like-for-like comparison, because each was re-derived from
scratch.

## 2. Verdicts on the six previously reported findings

### 2.1 [FIXED — verified] bubblewrap `--tmpfs /tmp` no longer breaks OCR under `/tmp`

`a31bd0f` adds an `extra_ro_binds` parameter to `visual_ocr.py::_run` that re-exposes the
input's parent directory read-only *after* the `--tmpfs /tmp` that used to mask it, and applies
it to both affected call sites (`_ocr_image` for canonical originals, `_render_pdf_page` for
PDF page rendering).

Verified empirically, not just by reading the diff — with `ARCHIV_HOME` under `/tmp` and the
sandbox at its default setting:

| Probe | Result |
|---|---|
| Photograph → image OCR | `archiv.visual-ocr` **succeeded** |
| Scanned PDF (no text layer) → page-render OCR | `archiv.visual-ocr` **succeeded** |
| Native-text PDF | `archiv.visual-ocr` correctly **skipped** |

Previously every page failed with `cannot read input file … No such file or directory`. This
finding is closed.

### 2.2 [STILL OPEN] `ask` emits fabricated claims that pass citation verification

Re-probed with the two anti-hallucination questions from the corpus manifest, run repeatedly
because the previous report correctly noted this is model- and prompt-sensitive — a single run
proves nothing either way.

**Probe 1** (fabricated bilateral agreement, no such content in the corpus) — 5 runs:

| Run | Outcome |
|---|---|
| 1 | **Fabricated.** Prose paragraph asserts the agreement was finalized, cites a source — while the response's own `insufficient_evidence` field simultaneously states the information is missing. Internally contradictory output. |
| 2 | Correct refusal |
| 3 | **Fabricated.** A structured *claim* (not merely prose) asserting the agreement was finalized on a specific date, citing two sources, with an invented corroborating naval detail. |
| 4, 5 | Correct refusal |

**Probe 2** (false-premise question about treaties that do not exist) — 3 runs: 2 correct
refusals; 1 rejected fail-closed with `partially_produced_but_invalid` because the model
emitted `"contradictions": null` instead of `[]` — the underlying text was also fabricating, so
the schema check caught it for an unrelated reason.

So: **2 of 8 runs shipped an affirmative fabricated statement with citations attached.** The
previous run saw this on the *other* probe, which supports the "model-sensitive, not
systematic" reading — but it is clearly reproducible rather than a one-off.

The user-facing rendering is the aggravating factor. Every answer, including correct refusals,
prints a **`Verified Sources`** block listing all 8 retrieved citations. When the answer is a
fabrication, the reader sees an affirmative claim directly above eight file/paragraph
references under a heading that says "Verified". The verification is real but narrow — it
confirms the citations exist and hash-match, not that they support the claim — and the label
does not convey that distinction.

This matches the documented design, and upstream has left it open pending a design decision.
The suggestion from the previous report still stands (entailment spot-check, or requiring a
quote substring per claim). One cheaper interim mitigation: when `insufficient_evidence` is
non-empty, the renderer could suppress or visibly qualify the `Verified Sources` heading —
run 1 above produced a response that contradicted itself in two adjacent fields, and the
renderer had all the information needed to notice.

### 2.3 [STILL LEAKED — and CI cannot detect it] The account-name literal is back

The fix commit correctly parameterized the account-name literal out of the shell tool and the
two docs that had copy-pasted it. But the *same commit* added a field-notes document that
describes the leak — and in describing it, writes the literal out again.

- Offending location: `docs/field-notes-2026-08-23-real-world-corpus.md`, line 34 (one
  occurrence, one file — the literal is deliberately not reproduced here for the same reason).
- Suggested fix: refer to the account name generically in that sentence, exactly as the three
  remediated files now do.

**The more important half of this finding is why nobody noticed.**
`tests/test_privacy_and_artifacts.py::test_no_private_paths_or_secrets_in_tracked_files` derives
the string it searches for from the *running machine's* `$USER`, and returns early — passing
vacuously — when that value is empty, shorter than three characters, or one of
`runner`, `root`, `ubuntu`, `user`, `runneradmin`:

```python
user_name = os.environ.get("USER", "")
if (
    not user_name
    or len(user_name) < 3
    or user_name.lower() in {"runner", "root", "ubuntu", "user", "runneradmin"}
):
    return
```

GitHub-hosted runners execute as `runner`. **The check therefore never runs in CI** — it
short-circuits on every hosted job, on every branch, forever. `Fast checks` has passed on `main`
continuously, including on the commit that introduced the leak, and would keep passing no matter
how many usernames were committed.

The only machine on which this guardrail does anything is one whose `$USER` happens to match a
name already present in the tree — i.e. the developer who leaked it, and only until they change
machines. That is how the literal survived a commit whose stated purpose was removing it.

So the accurate status is: the repo's tracked files still contain a contributor's account name,
in a public repository, and the automated control designed to prevent exactly that is inert
where it matters. Observed here only because this evaluation ran on the affected account.

**Suggested fix**, beyond the one-line doc edit: make the check independent of the runtime
environment. A committed deny-list of known-private literals, or scanning for a pattern of
`/home/<name>` and `/Users/<name>` path shapes, would run identically everywhere. As written the
test's own skip conditions guarantee it can never fail in the one place the project relies on it.

### 2.4 [STILL OPEN — severity reduced] `report` fails wholesale with small local models

Reproduced, 2 attempts out of 2 on the model-backed path:

```
report failed: inline citation [1] missing from its finding; … [8] missing from its finding
```

Fail-closed is the right behaviour; the problem remains that the inline-citation contract is
too strict for a small local model to satisfy, so report generation is effectively unavailable.

**New this round:** `archiv report --deterministic` bypasses model synthesis and **succeeds** —
it produced a validated DOCX with 4 citations, and `archiv verify <task-run-id>` on it returned
`valid: true`. The previous report did not mention this flag. That materially lowers the
severity: report generation is *available* on small models, just not the model-synthesized
variant. It deserves to be documented as the recommended path for small local models.

Two residual issues:

1. The failure is still hard to debug after the fact. `ask` run evidence persists
   `model_response.txt`; `report` task-run evidence persists only `request.json` and
   `retrieval.json` (plus `result.json` on success). A failed report leaves no record of what
   the model actually returned.
2. `--deterministic` inherits the strict-phrase retrieval semantics (§2.5). A single-term
   objective worked; a plain-English multi-word objective failed with
   `report generation requires at least one search result`, because the whole objective is
   matched as one FTS phrase.

### 2.5 [STILL PRESENT — cause attributed] Retrieval crowding

Re-ran the 22-question manifest (20 scored + 2 refusal probes) with `find --limit 100`:
**11 of 20 questions surfaced the expected source**, against 10 of 20 previously. Roughly flat,
and not directly comparable since the corpus changed.

The nine misses were then classified by asking a question the previous report did not: *does
the expected source actually contain the term at all?* (`search <fact> --source-name <expected>`)

| Cause | Count | Notes |
|---|---|---|
| **Crowded out of top-100** | 4 | The term *is* in the expected source, but bm25 ranks 100+ other segments above it |
| Content genuinely absent from the extracted text | 5 | Of which: 1 caused by finding A below, 2 by OCR quality on tabular/handwritten scans, 2 by manifest defects |

**A controlled experiment settles the cause — and it is not what the previous report or the
upstream field notes assumed.** Both concluded the crowding was primarily a test-corpus defect,
caused by a 23.9 MB file that concatenates 1,867 of the same daily reports already present
individually, supplying **148,190 of 441,809 segments — 33.5% of the entire index**.

That hypothesis was tested directly: the corpus was re-ingested into a separate archive with
that one file excluded (hardlink mirror, source corpus untouched), and the identical 22-question
benchmark re-run.

| Metric | With digest | Without digest |
|---|---|---|
| Index segments | 441,809 | 293,619 (−33.5%) |
| Questions retrieving expected source | **11 / 20** | **11 / 20** |
| Questions whose rank improved | — | 4 (best: rank 9 → 4) |
| Questions whose rank worsened | — | 0 |
| Facts newly entering top-100 | — | 3 |
| `find`, common term, `--limit 100` | 4.4–4.6 s | 3.4–3.6 s |

**Zero misses became hits.** Removing a third of the index improved ranks for questions that
already passed and made nothing worse, but it did not fix a single one of the four crowded-out
questions. Repairing the corpus is worth doing — it is real, measurable overhead — but it is
**not** sufficient, and the earlier recommendation to fix the corpus *before* looking at
retrieval was wrong.

Inspecting what actually occupies the top-100 after the digest is gone shows the real mechanism:

| Query | Top-100 composition |
|---|---|
| District name (q08) | **98 of 100 slots held by 3 spreadsheets** (34 / 32 / 32) |
| Common noun (q07), place name (q13) | 100 distinct sources, one slot each |

**`find` and `search` apply no per-source diversity cap.** Ranking is purely per-segment, so a
file that contains the term in many segments floods the result set, while a file that contains
it once contributes one slot. Because spreadsheets are segmented per cell, three spreadsheets
can occupy 98% of the result window and push the expected roster — which *does* contain the
term — past the 100-result ceiling. The concatenated digest was simply a third instance of the
same mechanism, not a distinct problem.

Notably, the machinery to fix this already exists: `reports/generator.py:39` defines
`distinct_sources(results, *, limit)` and applies it when selecting sources for `report` and
`ask`. It is never applied to `find`/`search`.

The q07/q13 case is different again and is *not* fixable by ranking: roughly 1,800 near-identical
daily reports each mention these common terms once, so there is no signal that distinguishes the
one the manifest expects. Those questions are underspecified rather than mis-retrieved.

**Revised suggestion,** in priority order: (1) apply a per-source slot cap to `find`/`search`,
reusing `distinct_sources`, and/or down-weight very short tabular segments; (2) allow `--limit`
above 100, or expose a per-source cap flag; (3) repair the corpus (worth ~4 rank positions and
~23% query latency, but no score change).

Three design properties worth documenting explicitly (all re-confirmed, all surprising in
practice):

- Multi-word `find` is a strict FTS **phrase** match, not an AND of terms. This is what makes
  plain-English objectives fail in `report` (§2.4).
- Spreadsheet cells are individual segments, so a phrase spanning two cells can never match.
- Filenames are filter columns, not indexed content — a fact that appears only in a filename is
  unreachable via `find`.

### 2.6 Minor items — mixed progress

| Previous item | Verdict |
|---|---|
| `verify` on an `ask` run ID raises a raw traceback | **Still present.** A valid 32-hex ask-run ID prints a full framed traceback ending in `FileNotFoundError: unknown task run: …`; `verify` supports task runs only. Exit code is 1 (correct). A real task-run ID verifies cleanly. |
| `restore --home` contradicts its own help | **Partly fixed.** Help now marks `--home` as required and no longer promises an `ARCHIV_HOME` default. But `README.md:455` still lists the command as `archiv restore PATH`, with no mention of the required flag. `ARCHIV_HOME` is not documented in the README at all. |
| Missing `--json` on `ingest`/`search` | **Re-characterized.** These commands *always* emit JSON, so passing `--json` is rejected with exit 2. The real problem is an inconsistent surface, not absent functionality: 13 of 23 commands accept `--json`; some of the remaining 10 emit JSON unconditionally (`ingest`, `search`, `verify`), others never do. `rebuild-search-index` also rejects `--json`. |
| Batch `add` aborts on all-invalid folders with one generic line | **Still present**, and see finding G. |
| `benchmark-ocr` needs undocumented env vars | **Still undocumented** — the font env vars appear only as an unchecked TODO in the field notes, not in `--help` or the README. |
| Default `ask` timeout too low for some models | **Not retested.** `configure-loopback --timeout` exists and was set to 300 s; `granite4:micro` completed every run in 40–81 s, well inside even the 120 s default. The larger model that previously exceeded the budget was not re-run this round. |

## 3. New findings

### 3.1 [HIGH] Scanner-watermark PDFs are ingested "successfully" with zero recoverable content

**Symptom.** A scanned PDF whose only embedded text is a scanner app's watermark is ingested,
reported as `succeeded`, and is effectively unsearchable. Nothing in the output indicates that
the document's actual content was never read.

**Scale in this corpus — 6 of 20 valid PDFs (30%), 28 pages of content lost:**

| Pages | OCR status | Segments | Chars/page |
|---|---|---|---|
| 3 | skipped | 3 | 10 |
| 3 | skipped | 3 | 10 |
| 1 | skipped | 1 | 10 |
| 4 | skipped | 4 | 10 |
| 14 | skipped | 14 | 10 |
| 4 | skipped | 4 | 10 |

Exactly 10 characters per page, on every one: the length of the scanner app's watermark string.
`pdftotext` confirms the text layer contains nothing else.

**Root cause.** `src/archiv/ingestion/visual_ocr.py:374-388` — the decisive lines are 383-387:

```python
def _pdf_pages(document: NormalizedDocument) -> tuple[int, list[int]]:
    page_count = document.metadata.get("pages")
    if not isinstance(page_count, int) or page_count < 0:
        raise VisualOcrError("normalized PDF does not report a valid page count")
    text_by_page: dict[int, list[str]] = {}
    for segment in document.segments:
        page = segment.locator.get("page")
        if isinstance(page, int):
            text_by_page.setdefault(page, []).append(segment.text)
    empty = [
        page
        for page in range(1, page_count + 1)
        if not "\n".join(text_by_page.get(page, [])).strip()
    ]
    return page_count, empty
```

A page is considered to have usable native text if its text is non-empty after `.strip()` — a
single non-whitespace character is enough. `run_visual_ocr` then short-circuits the whole
document with `reason: "native text is available for every PDF page"`.

**Why this matters more than the page count suggests.** Watermarking is the default behaviour of
the common phone-scanner apps, so this failure targets precisely the class of document that
most needs OCR. The failure is silent: exit code 0, status `succeeded`, no warning. A user
would only discover it by noticing that searches never return the document. In this corpus it
also caused one manifest question to be unanswerable (§2.5).

**Aggravating factor:** there is no way to override it. `ARCHIV_OCR` (`visual_ocr.py:442`) only
recognises off-style values (`0`/`off`/`false`); anything else means "auto". There is no
`force`, and no per-file flag, so a user who *does* notice cannot make Archiv OCR the file.

**Suggested fix.** Treat a page as image-only when its native text falls below a small density
threshold (characters or alphanumeric tokens per page) rather than when it is exactly empty.
The per-page structure to do this already exists — `_pdf_pages` already returns a per-page list,
so the change is local. Worth pairing with an `ARCHIV_OCR=force` escape hatch and surfacing
`pages_requiring_ocr` (already recorded in the manifest) in the ingest summary.

### 3.2 [MEDIUM] An interrupted ingest leaves state that a re-run cannot repair, and misreports it

**Reproduction** (deterministic):

```bash
archiv ingest FILE --home H            # succeeds
rm -rf H/derived/<sha256>/normalized   # the state an interrupted ingest leaves behind
archiv add FILE --home H               # rc=1: "no supported valid files could be ingested"
archiv add FILE --home H --rebuild-derived   # rc=0, repaired
```

When an object row exists but its normalized derived document does not, re-adding that file
fails with `FileNotFoundError: existing object has no normalized derived document`, surfaced to
the user as:

```
add failed: no supported valid files could be ingested
```

**The message is actively misleading.** The file is supported *and* valid; what is broken is the
archive's own derived state. The message names neither the real cause nor the fix, and
`--rebuild-derived` — which repairs it in one step — is not suggested.

This was first observed as a real, unprompted failure during this evaluation (§4), not
constructed: an ingest of the full corpus left one presentation file in exactly this state, and
the subsequent pass failed on it. Given that a full ingest of this corpus takes over five
minutes, a user interrupting one is not an exotic scenario.

**Suggested fix.** Detect the missing-derived case and either self-heal (the original is
immutable and still present, so the derived data is reconstructible by definition) or fail with
a message that names the object and recommends `--rebuild-derived`.

### 3.3 [LOW] Batch `add` reports rejected-but-supported files as "unsupported", and claims zero failures

The full-corpus run reported:

```
added: 2062   duplicates: 5   skipped_unsupported: 35   status: succeeded
Ingestions: 2062 succeeded, 5 duplicate, 0 failed
```

`skipped_unsupported` is a bare integer, and it conflates two different things. Of those 35:

- **26** have genuinely unsupported extensions — correct, and correctly rejected up front.
- **9 have supported extensions and were rejected as malformed**: one Word lock file (`~$…docx`),
  the three deliberate edge-case files, and five zero-byte JPEGs.

Each of those 9 fails cleanly and informatively when ingested individually (`MalformedInputError:
PackageNotFoundError`, `EmptyFileError`, and so on). In a directory `add`, all of that
diagnostic detail is discarded: no filenames, no reasons, and a summary line that says
`0 failed`. A user adding a large folder has no way to learn which files did not make it in, or
why, without re-running `ingest` per file.

To be clear, the *behaviour* is right — fail-closed held on every one of them, and nothing
partial was written. This is purely a reporting defect, but it is the difference between
noticing and not noticing that 9 documents are missing from an archive of 2,057.

### 3.4 [UNRESOLVED ANOMALY — reported as evidence only]

The first full-corpus ingest of this session behaved in a way I could not reproduce or explain,
so it is recorded here as raw evidence rather than a diagnosed finding.

A single `archiv add` invocation produced **4,022 ingestion rows for 2,097 files**: 1,960 source
paths were ingested twice — once normally, once flagged as duplicates — while the 102
image/OCR files were ingested once. The summary consequently reported `new_originals: 100` and
`duplicates: 1961` on a home that was empty when the run started, against a corpus with only 10
genuinely duplicate files. One presentation file was left with an orphaned `pending` ingestion
row and a `failed` row (the missing-derived error that became §3.2). The database timestamps
span 7m40s while `/usr/bin/time` reported 2m37s elapsed for the command.

It did not recur. A second clean run of the identical command on a fresh home produced exactly
the right numbers (§5), and three scaled probes — 7 files, 34 files including the corpus's
largest file, 45 mixed files including images — each produced a single correct walk. I could not
identify a trigger, and there is no OOM record in the kernel log. Reported so the evidence is
not lost; not counted as a defect.

## 4. What works — verified by measurement

- **Full-corpus ingest:** 2,097 files → 2,062 ingested, 5 duplicates (exactly the corpus's true
  content-duplicate count, independently verified by hashing), 35 skipped, **0 failures**.
  2,057 objects / 441,809 segments. 5m23s, peak RSS 1.06 GB.
- **Format coverage on real files:** DOCX, native-text PDF, scanned PDF, XLSX (cell-level
  segments), PPTX, the ODF family, `.odb` (metadata only), InPage `.inp` (native Urdu, verified
  searchable), JPG/PNG via sandboxed OCR, WAV catalogued as audio.
- **Fail-closed validation:** corrupted zip DOCX → `MalformedInputError: PackageNotFoundError`;
  zero-byte PDF → `EmptyFileError`; decompression-expansion DOCX rejected. All exit 1, nothing
  ingested, no crash, originals untouched.
- **Unsupported formats** rejected up front by extension with clear errors.
- **OCR at scale:** 136 successful visual-OCR runs, 11 correctly skipped; OCR'd text verified
  searchable and correctly attributed to its source image.
- **Privacy boundary:** HTTPS endpoint rejected (`local model endpoint must use plain HTTP on
  loopback`); a `/v1` path suffix rejected; plain loopback accepted. Verified again this run.
- **Backup / restore round-trip:** backup 544 MB in 17.2 s; restore into an empty home in 22.7 s
  reproduces **identical** counts — 2,057 documents / 2,062 ingestions / 5 duplicates / 0 failed
  / 441,809 passages — and identical on-disk size (1.1 GB both). The previous run's 4×
  restored-size discrepancy did **not** reproduce.
- **Atomic index rebuild is deterministic:** 9.7 s for 441,809 passages, and the rebuilt index's
  SHA-256 is **byte-identical** to the one the original ingest produced
  (`b6a6dad642be2845…`). `find` results identical in set and order before and after.
- **Citation chain:** `find --json` → `source --citation-file … --citation-number N` returned
  `Validated: immutable original and citation`, in read-only mode, resolving to the correct
  paragraph locator and the preserved original's content-addressed path.
- **`report --deterministic`** produces a DOCX that passes independent `verify` (`valid: true`).
- **Upstream suite:** 250 passed, 1 failed (§2.3).

## 5. Performance snapshot (this hardware, clean run)

| Operation | Result |
|---|---|
| Full ingest — 2,097 files / 498 MB | 5m23s, peak RSS 1.06 GB |
| Search-index rebuild — 441,809 passages | 9.7 s, byte-identical output |
| Backup (durable state) | 17.2 s → 544 MB |
| Restore + rebuild into empty home | 22.7 s, logical state identical |
| `find` — rare term (6 hits), `--limit 100` | 0.97 s |
| `find` — common term (result set caps at 100) | **4.4–4.6 s** |
| `ask` (granite4:micro, CPU) | 40–81 s |
| Upstream pytest suite | 53 s |

Worth flagging: a common-term `find` takes roughly 4.5× longer than a rare-term one on this
441,809-segment index. That is the same crowding described in §2.5 showing up as latency rather
than as bad ranking — the query has to score a very large candidate set, a third of which comes
from a single duplicated file. It is a second, independent reason to fix the corpus before
tuning the ranker.

## 6. Suggested tracker items

1. `bug(ocr)`: watermark-only text layer suppresses OCR — density threshold + `ARCHIV_OCR=force` (§3.1) — **highest value fix in this report**
2. `bug(ingest)`: missing derived data is unrecoverable via plain `add`, and the error message misdirects (§3.2)
3. `bug(ci)`: privacy guardrail is inert on hosted runners (self-skips on `$USER == runner`), and the literal it was meant to remove is back in the fix commit's own field notes (§2.3)
4. `quality(ask)`: fabricated-claim pass-through; consider qualifying `Verified Sources` when `insufficient_evidence` is non-empty (§2.2)
5. `docs(report)`: document `--deterministic` as the supported path for small local models; persist raw model responses for report runs (§2.4)
6. `ux(add)`: per-file diagnostics for rejected files; stop bucketing malformed-but-supported files as "unsupported" (§3.3)
7. `docs`: `README:455` restore signature, `ARCHIV_HOME` undocumented, `benchmark-ocr` font env vars, `--json` surface consistency (§2.6)
8. `feat(search)`: apply a per-source slot cap to `find`/`search` — `distinct_sources` already exists in `reports/generator.py:39` but is never used here; consider down-weighting very short tabular segments and allowing `--limit` > 100 (§2.5)
9. `test-data`: see the companion [corpus-quality notes](field-notes-2026-08-24-test-corpus-quality.md) — worth repairing for latency and rank quality, but measured **not** to change the benchmark score

*All figures reproduced from a clean clone and a clean archive home; logs, per-question retrieval
results, run evidence, and probe outputs retained locally. Corpus content is redacted throughout:
this corpus contains real personal and security-sensitive records, and no document text, place
name, personal name, or filename from it appears in this report.*
