# Archiv 0.1.0a6 — Third field evaluation on a real-world corpus

> **Status: nascent / informal.** A third ad hoc run against the same real-world corpus as
> [`field-notes-2026-08-23-real-world-corpus.md`](field-notes-2026-08-23-real-world-corpus.md)
> and [`field-notes-2026-08-24-real-world-corpus-retest.md`](field-notes-2026-08-24-real-world-corpus-retest.md).
> Not the frozen, CI-measured benchmark in [`field-trial-report.md`](field-trial-report.md).
> Single machine, single pass, and a test corpus with its own quality issues (see
> [`field-notes-2026-08-24-test-corpus-quality.md`](field-notes-2026-08-24-test-corpus-quality.md)).
> Treat as a triage list, not a certified result.

**Tested:** `Nan0pk/Archiv` @ `3e0703d` (`main`, 21 commits past the previous evaluation's `a31bd0f`; the repository squash-merges, so each is a landed PR), branch cut from `origin/main`, dependencies reinstalled to match `pyproject.toml` at that commit (`pypdf` 6.16.2, `reportlab` 5.0.1)
**Platform:** Fedora (x86_64, kernel 7.1.8), Python 3.13.14 (uv venv), SQLite 3.53.1 with FTS5, tesseract 5.5.3 (`ara eng urd`), bubblewrap 0.11.0, LibreOffice 26.2.5.2
**Corpus:** 2,097 real-world files, 498 MB — 2,071 with a supported suffix, 26 with an unsupported one. Daily-report DOCX (1,869 files), native and scanned PDFs, XLSX, PPTX, the ODF family, InPage `.inp`, photographs including handwritten sheets, RTL text in Urdu/Pashto/Dari/Arabic, WAV audio, plus deliberately malformed and zero-byte fixtures
**Model backend:** none configured — `ask` and `report` were **not** exercised this round
**Archive home:** a fresh, empty directory on persistent disk (`/home`), deliberately not under `/tmp`
**OCR:** `ARCHIV_OCR` left at `auto`; visual OCR ran (136 manifests succeeded, 10 skipped). The `ocr-benchmark` extras (`onnxruntime`, `rapidocr`) were **not** installed, so `archiv benchmark-ocr` was not exercised.

---

## 1. Executive summary

The five commands `CONTRIBUTING.md` names as the acceptance contract were run first. Four pass
clean. `pytest` fails one test, and it is the same environment-dependent privacy test that
failed on 2026-08-23 — not a regression (§2.4).

| Gate | Result |
|------|--------|
| `archiv doctor` | **pass** — 3/3 checks (`python`, `sqlite_fts5`, `writable_workspace`) |
| `ruff format --check .` | **pass** — 213 files already formatted |
| `ruff check .` | **pass** — all checks passed |
| `pyright` (strict) | **pass** — 0 errors, 0 warnings, 0 informations |
| `pytest` | **1 failed, 285 passed** in 55.8 s — pre-existing, see §2.4 |

The core guarantees held again at scale. The new durable-storage integrity framework (#92)
reports a clean archive, and the search-index rebuild is still **byte-for-byte deterministic**.
Finding G of the previous round is **fixed and verified by exact arithmetic**, and the
interrupted-ingest artifact of §3.2 did not recur.

Material findings this round:

| # | Severity | Finding | Status vs. previous run |
|---|----------|---------|--------------------------|
| A | **High (regression — content loss)** | The 250-page cap introduced by #94 is enforced on the **native PDF text path**, not just OCR fallback. One 511-page, 3.4 MB real PDF that ingested successfully at `a31bd0f` is now hard-rejected. No override flag. `docs/visual-ocr.md` still describes the cap as OCR-only. | **New** |
| B | **High (diagnosability)** | Ingestion failures are counted in-process and never persisted. `add` reports `failed: 10` with no path, error, or reason; `status` and `diagnostics-export` then report `failed: 0` and `error_categories: {}`. The `ingestions` table already has `status` and `error` columns and no failed row is ever written. | **New** |
| C | Medium (false statement about the archive) | "Partially searchable (degraded)" is assigned from format-family classification alone. 152 of the 174 files flagged degraded had **no** skipped processor — their extraction was complete. | **New** |
| D | Medium (JSON contract) | `rejected_unsupported` and `skipped_unsupported` are emitted with the same value; `skipped_unsupported` silently changed meaning (35 → 26) between commits. `degraded` has two different definitions, giving 174 from `add` and 169 from `status` for the same archive. | **New** |
| E | **High (quality)** | Scanner-watermark PDFs still lose 100% of their content — 6 of 19 valid PDFs, at exactly 10 characters per page. | Previously 3.1 — **still open, unchanged** |
| F | Medium (retrieval) | `find`/`search` still apply no per-source diversity cap: one source took 9 of 20 result slots. | Previously 2.5/F — **still open** |
| G | Medium (test design) | The privacy guardrail is structurally unpassable for some contributor account names, scans untracked files, and still self-skips on hosted runners. | Previously 2.3/C — **unchanged** |
| H | Low (JSON contract) | Every command now emits machine-readable output, but by two different conventions, and the array-shaped payloads carry no `schema_version` envelope. | Previously 2.6/H — **substantially resolved** |

### Fixed and verified

- **Previous finding G — batch `add` reported rejected-but-supported files as "unsupported" and
  claimed zero failures. Now correct**, confirmed against ground truth (§2.1).
- **Previous §3.2 — the interrupted ingest that misreported `duplicates: 1961` / `new_originals: 100`.**
  A clean run reproduces `duplicates: 5` / `new_originals: 2056`, and the 73.9 MB PPTX that
  was left in an orphaned `pending` state now ingests. The previous report's diagnosis is
  confirmed (§2.2).

### A note on comparing numbers with the previous report

The previous report contains **two** full-corpus ingest logs and only one of them is a valid
baseline. The 2:37 wall-clock figure comes from the interrupted run described in its §3.2; the
clean reference is 5:23. **The like-for-like pair is 5:23 (`a31bd0f`) vs 5:25 (`3e0703d`) —
there is no performance regression.** A reader comparing against the wrong log would conclude
otherwise.

## 2. Verdicts on the previously reported findings

### 2.1 [FIXED — verified] Batch `add` no longer hides failures inside the "unsupported" count

Previous finding G was that rejected-but-supported files were folded into an aggregate
"unsupported" count while the run reported "0 failed". The corpus lets this be checked exactly,
because every bucket has an independently derivable ground truth.

Ground truth, by hashing the corpus directly: 2,097 files = **2,071** with a supported suffix +
**26** with an unsupported suffix (`.accdb .chm .db .doc .json .kml .m4a .mp3 .opus .rtf .sqlitedb`).
Of the 2,071, **9** are deliberately malformed or zero-byte fixtures that cannot be extracted.

| Run | commit | added | reported unsupported | reported failed | Reconciles? |
|-----|--------|-------|----------------------|-----------------|-------------|
| previous, clean | `a31bd0f` | 2,062 | 35 | *(key absent)* | 35 = 26 unsupported **+ 9 malformed** — the misclassification |
| previous, interrupted | `a31bd0f` | 2,061 | 36 | *(key absent)* | 36 = 26 + 9 + 1 orphaned PPTX (§3.2) |
| **this run** | `3e0703d` | 2,061 | **26** | **10** | 26 = exactly the unsupported-suffix count; 10 = 9 malformed + 1 page-limit rejection (§3.1) |

`rejected_unsupported: 26` now matches the true unsupported-suffix count exactly, and the 9
malformed fixtures have moved out of that bucket into `failed`. That also resolves the 35 vs 36
discrepancy between the previous round's two logs: it is the one PPTX the interrupted run lost.

The counting is fixed. What the failures *say* is not — see §3.2.

### 2.2 [CONFIRMED — does not recur] The interrupted-ingest duplicate misreport

The previous §3.2 reported `duplicates: 1961` / `new_originals: 100` on a corpus with only 10
genuinely duplicate files, and attributed it to an interrupted earlier ingest leaving state a
re-run could not heal. This run, into a guaranteed-empty home:

```
duplicates: 5    new_originals: 2056    status: succeeded
```

5 is the true content-duplicate count among the *ingestible* files (the corpus has 10 duplicate
files; 6 of them are zero-byte and all 6 fail, so 5 duplicate ingestions remain). The previous
diagnosis is confirmed, and the 73.9 MB PPTX left orphaned by that run ingests normally here.

### 2.3 [STILL OPEN — unchanged] Scanner-watermark PDFs lose 100% of their content

Previous finding 3.1 reproduces exactly. Of the 19 PDF objects ingested, **6 normalize to
exactly 10 characters per page**, with segment count equal to page count:

| PDF objects | pages | segments | chars | chars/page |
|-------------|-------|----------|-------|------------|
| 6 affected | 1, 3, 3, 4, 4, 14 | = pages | 10 × pages | **10.0** |
| 13 others | 1 – 99 | 1 – 306 | 803 – 109,276 | 387 – 3,865 |

A 10-character scanner watermark is still accepted as a native text layer, so OCR is skipped and
the scanned content is never recovered. Unchanged from the previous round (6 of 20 valid PDFs
then, 6 of 19 now — the denominator moved only because of §3.1).

### 2.4 [STILL OPEN — unchanged] The privacy guardrail is unpassable for some account names

`tests/test_privacy_and_artifacts.py::test_no_private_paths_or_secrets_in_tracked_files` fails.
This is the *only* failing test, and the failure is byte-identical in substance to the one logged
on 2026-08-23 — it is not a regression, and it is not caused by anything in this report.

Three separate problems in one test:

1. **It self-skips on hosted runners.** `USER` is `runner` in CI, which is in the skip set, so
   the check is inert on every hosted job. Unchanged from previous finding C.
2. **It is unpassable for some contributor account names.** The test asserts the contributor's
   `$USER` literal appears in no file. This machine's account name happens to collide with the
   hardware model name that `docs/hardware-and-performance.md`, `docs/ocr-benchmark.md`,
   `docs/ocr-engine-comparison.md`, `docs/trial-protocol-v1.md` and
   `src/archiv/ocr_benchmark.py:196` document **on purpose** — 9 occurrences across 6 files.
   No contributor with that account name can ever get a green suite, and no code change fixes it.
3. **It scans untracked files.** Despite the local variable name `tracked_files`, the list is
   built with `REPO_ROOT.rglob("*")` and consults git not at all. Any scratch file left in the
   working tree is scanned, so the check can fail on content that is not part of the repository.

Suggested shape for a fix: derive the file list from `git ls-files`, and compare against the
*path* of the contributor's home directory rather than the bare account-name token.

### 2.5 [STILL OPEN] `find`/`search` have no per-source diversity cap

Previous finding F reproduces. A single-term `find` over the 441,298-passage index returned 20
results drawn from only **11 distinct sources**, with one source holding **9 of the 20 slots**
(45%). Ranking is still purely per-segment. Smaller effect than the previous round's 98-of-100
demonstration only because the default result limit is 20, not 100.

### 2.6 [SUBSTANTIALLY RESOLVED] `--json` coverage

Previous finding H reported partial machine-readable output. Checking stdout rather than
`--help` — the distinction matters, because several commands emit JSON with no flag at all —
every command reachable in this run produces machine-readable output:

| Convention | Commands |
|------------|----------|
| `--json` flag | `status` `add` `find` `backup` `export` `restore` `report` `ask` `formats` `doctor` `source` |
| JSON unconditionally, no flag exists | `search` `ingest` `rebuild-derived` `rebuild-search-index` `diagnostics-export` |

`archiv search` and `archiv ingest` in particular *look* like gaps from `--help` and are not:
`search` prints the same JSON array as `find --json`, and `ingest` prints the full
`IngestionResult` object. The previous round's "no JSON path" reading appears to have been drawn
from `--help` output.

Two residual defects, both cosmetic:

1. **Two conventions.** A caller cannot tell from `--help` whether a command needs `--json`, and
   passing it where it does not exist is a hard error (`No such option: --json`, exit 2).
2. **The array payloads have no envelope.** `find --json`, `search`, and `rebuild-derived` return
   bare JSON arrays with no `schema_version` at the top level, while every object-shaped payload
   in the CLI carries one. Elements carry their own `schema_version`, so the information is
   present, just not where a version check would look for it.

**`verify` was not exercised.** It takes a prior task-run ID, and with no model backend
configured this archive has zero report and zero ask runs, so there was nothing to verify. Its
output shape is untested here and should not be assumed either way.

### 2.7 Not re-tested this round

No model backend was configured, so previous finding B (`ask` emits fabricated claims that pass
citation verification) and previous finding E (`report` fails wholesale on small local models)
were **not exercised**. Neither should be treated as fixed on the strength of this report.

Also not exercised, and therefore not re-confirmed from the previous round:

- **`backup` / `export` / `restore`.** The previous report verified that backup/restore
  reproduces logical state exactly. This run relied on the new `status --json` integrity check
  instead (§4) and did not run a round-trip.
- **`verify`.** No task runs existed to verify (§2.6).
- **`benchmark-ocr`.** The `ocr-benchmark` extras were not installed.

## 3. New findings

### 3.1 [HIGH — regression, content loss] The 250-page cap now rejects native PDF text extraction

**One object regressed out of the archive between `a31bd0f` and `3e0703d`, and this is it.**

A 511-page, 3,450,728-byte real-world PDF is now rejected outright:

```
ingestion failed: MalformedInputError: LimitExceededError: pages limit exceeded: 511 > 250
```

At `a31bd0f` the same file (verified by content hash) ingested successfully, with a native text
layer and OCR correctly skipped:

```
normalized-document   archiv.normalizer     succeeded
extracted-text        archiv.text-export    succeeded
visual-ocr-manifest   archiv.visual-ocr     skipped
```

**Evidence that this is the whole of the difference.** Comparing the previous clean run with this
one, object and segment counts move by exactly this file:

| | previous clean run (`a31bd0f`) | this run (`3e0703d`) | delta |
|---|---|---|---|
| files added | 2,062 | 2,061 | **−1** |
| indexed documents | 2,057 | 2,056 | **−1** |
| indexed passages | 441,809 | 441,298 | **−511** |

−511 passages for a 511-page PDF, at one segment per page. Set-differencing the two runs'
`object_sha256` lists confirms it directly: **exactly one** object present before is absent now,
and it is this file. Nothing else regressed.

**Cause.** `9b5c69a` ("Harden ingestion and establish security release policy", #94) added
`src/archiv/ingestion/limits.py` with `MAX_PAGES = 250`, and wired `check_pages()` into
`src/archiv/ingestion/normalize_documents.py:46` — immediately after `reader.pages` on the
**native** extraction path:

```python
check_pages(len(reader.pages))
```

That is the only call site. Before #94, a 250-page bound existed only for OCR: `docs/visual-ocr.md:105`
still states the policy as *"250 maximum PDF pages for **OCR fallback**"*, and that document has
not been updated. So a bound whose documented purpose is capping expensive per-page rendering
now also refuses documents that need no rendering at all.

**Why this matters more than the other limits in `limits.py`.** The rest are hostile-input
bounds where rejection is the correct answer — zip bombs, symlinks, 256 MiB inputs, 80-megapixel
images. Page count is not that: a long PDF with a native text layer is ordinary, its extraction
cost is linear and modest, and 511 pages is unremarkable for a report or a scanned book. The
corpus contains exactly one such file and it is a real document, not a fixture.

**Compounding factors.**

1. **No override.** There is no flag, environment variable, or config key that raises or waives
   `MAX_PAGES`. `MAX_INPUT_BYTES` and the archive bounds are equally fixed, but those have a
   defensible security reading; this one leaves the user with no path to their own document.
2. **The batch run reports success.** `archiv add` exited **0** with `"status": "succeeded"`
   while dropping the file. A user ingesting a folder is not told a document was refused unless
   they notice a bare count (§3.2).
3. **The message says the wrong thing.** `MalformedInputError` wrapping `LimitExceededError`
   tells the user their file is malformed. It is not — it is well-formed and too long.

**Suggested fix:** either restrict `check_pages()` to the OCR path it was documented for and add
a separate, much higher bound for native extraction, or keep the cap and add an explicit opt-out
plus a distinct error class that does not claim malformation. Either way `docs/visual-ocr.md`
needs to match the code.

### 3.2 [HIGH — diagnosability] Ingestion failures are counted and then discarded

`add` now correctly reports **that** files failed (§2.1). It reports nothing about **which** or
**why**, and the information is destroyed rather than merely unshown.

`src/archiv/user_cli.py:88-89`:

```python
except (OSError, RuntimeError, ValueError):
    failed += 1
```

The exception object is bound to nothing. The path is in scope (`active`) and is not recorded
either. Every failure therefore collapses to `+1`.

That the reasons exist and are specific is easy to show — ingesting the same 10 files one at a
time yields a precise, distinct diagnosis for each:

| Failures | suffix | size | error |
|---|---|---|---|
| 1 | `.pdf` | 3.4 MB | `LimitExceededError: pages limit exceeded: 511 > 250` (§3.1) |
| 1 | `.pdf` | 0 B | `EmptyFileError: Cannot read an empty file` |
| 5 | `.jpg` | 0 B | `UnidentifiedImageError: cannot identify image file` |
| 2 | `.docx` | 40 B, 162 B | `PackageNotFoundError: Package not found` |
| 1 | `.docx` | 487 B | `AttributeError: 'lxml.etree._Element' object has no attribute 'overrides'` |

All ten are wrapped as `MalformedInputError`, which for nine of them is accurate.

**The loss propagates into every other diagnostic surface.** The failures are never written to
the database, so every downstream consumer reports a clean archive:

| Surface | What it says after a run that failed 10 files |
|---------|-----------------------------------------------|
| `archiv add --json` | `"failed": 10` — a bare integer, exit code 0, `"status": "succeeded"` |
| `archiv add --summary-out` | `"failed": 10` — aggregate-only by design, correct |
| `archiv status` | `Ingestions: 2061 succeeded, 5 duplicate, **0 failed**` |
| `archiv status --json` | `ingestion_summary.failed: 0`, `rejected: 0`, `errors: []` |
| `archiv diagnostics-export` | `"error_categories": {}`, `ingestion_states: {"succeeded": 2061}` |

The support bundle whose stated purpose is diagnosing problems structurally cannot show the
problems.

**The schema already supports the fix.** The `ingestions` table is declared as:

```
ingestion_id, object_sha256, source_path, source_name, imported_at, duplicate, status, error
```

`status` and `error` are there and intended for this. `SELECT status, COUNT(*) FROM ingestions`
returns a single row — `succeeded 2061`. The `status` query at `user_cli.py:161-162` even counts
`WHERE status = 'failed'`, a branch that can never be non-zero because nothing writes it.
Writing a failed row inside the `except` block would light up `status`, `diagnostics-export`, and
the `failed_ingestions` query at once.

**Narrow observation on the handler.** `except (OSError, RuntimeError, ValueError)` does not
catch `KeyError`, `TypeError`, or `AttributeError` raised outside a wrapper, so a file failing
that way would abort the whole batch rather than be counted. No corpus file did this — the lxml
`AttributeError` above arrives wrapped as `MalformedInputError`, a `ValueError` subclass — so
this is a reading of the handler, not observed behaviour.

### 3.3 [MEDIUM] "Partially searchable (degraded)" is decided by file extension, not by what happened

`archiv add` printed `Partially searchable (degraded): 174`. Of those 174 files, **152 had no
skipped processor at all** — their extraction ran to completion with nothing degraded about it.

`src/archiv/user_cli.py:85`:

```python
degraded += active.suffix.lower() in _partial_suffixes() or processor_skipped
```

`_partial_suffixes()` is every suffix in a format family whose matrix `support_level` is
`partial`: `.pdf .xlsx .ods .ots .fods .odp .otp .fodp .odb .inp .png .jpg .jpeg .wav`. So the
label is applied to a **family classification**, and only incidentally to an observed outcome.
Decomposing the 174 from the run's own JSON:

| Reason flagged | count |
|---|---|
| partial-family suffix only — nothing was skipped | **152** |
| partial-family suffix **and** a skipped processor | 20 |
| skipped processor only | 2 |
| **total reported as degraded** | **174** |

**This corpus badly under-shows the problem.** It is 1,869 `.docx` out of 2,097 files, and
`office-word` is classified `FULL`, so only 8% of the corpus can be mislabelled. Invert the mix
— a user with 500 clean, fully-extracted native PDFs — and Archiv tells them all 500 documents
in their archive are only partially searchable. That is a false statement about the user's data,
made in the user's own words rather than in matrix vocabulary.

The distinction is already available: `processor_skipped` is computed on the line above, and
`status --json` reports `skipped` as its own counter. Suggested fix: count `degraded` from
`processor_skipped` alone, and if the family classification is worth surfacing, surface it under
its own name.

### 3.4 [MEDIUM] Two JSON contract defects in the new counters

**(a) `rejected_unsupported` and `skipped_unsupported` are the same value.** `user_cli.py:304-305`:

```python
"rejected_unsupported": rejected,
"skipped_unsupported": rejected,
```

Two keys, one variable. `skipped_unsupported` is presumably retained for compatibility, but its
value changed meaning between commits: it was 35 at `a31bd0f` (unsupported + malformed) and is
26 here (unsupported only). A consumer reading that key sees the number move by 9 with no schema
change to signal it, while the genuinely-skipped count (22) is reachable only through the new
`ingestion_summary` object. Either drop the alias or make it a real deprecation.

**(b) `degraded` has two definitions, and they disagree on the same archive.** `add` reported
174; `status` reports 169 for the identical archive, seconds later. They are computed
differently:

| | `add` (`user_cli.py:85`) | `status` (SQL, `user_cli.py:166`) |
|---|---|---|
| unit | per **ingestion** (2,061 rows, duplicates included) | per **object** (2,056 rows) |
| rule | partial-family suffix **or** processor skipped | `source_extension IN (partial suffixes)` only |

So the two differ on both axes. Reconciled against the database: 169 objects carry a
partial-family extension; `add` additionally counts 3 duplicate ingestions of partial-family
objects, plus the 2 files that were skipped-only without a partial suffix — 169 + 3 + 2 = **174**.
Both numbers are internally consistent and neither is wrong on its own terms; they simply cannot
both be called `degraded` in a field named `ingestion_summary.degraded` with `schema_version: 1`.

## 4. What works — verified by measurement

- **The acceptance gate.** `archiv doctor` 3/3, `ruff format --check .` clean over 213 files,
  `ruff check .` clean, `pyright` strict with 0 errors. Test count has grown from 251 to 286.
- **Full-corpus ingest:** 2,097 files → 2,061 ingested, 5 duplicates (the true content-duplicate
  count among ingestible files, independently verified by hashing), 26 correctly rejected as
  unsupported, 10 failed (9 malformed fixtures + §3.1). 2,056 objects / 441,298 passages.
  **5m25s**, peak RSS 1.20 GB — statistically indistinguishable from the previous clean run's
  5m23s / 1.06 GB.
- **The new integrity framework (#92) reports a clean archive.** `status --json` →
  `integrity.ok: true`, 2,056 canonical objects checked with **0 corrupt**, 2,348 evidence
  records checked with **0 invalid**, `database: "ok"`, no orphaned temporaries.
- **Search-index rebuild is byte-for-byte deterministic.** Rebuilding from scratch reproduced
  SHA-256 `188ce4f9…` — identical to the index the ingest itself produced — with the same 2,056
  documents and 441,298 passages, in 10.8 s.
- **Visual OCR ran correctly** on persistent-disk storage: 136 manifests succeeded, 10 skipped.
  The `/tmp`-masking fix from `a31bd0f` continues to hold.
- **`diagnostics-export` (#91) is genuinely privacy-clean.** The bundle contains dependency
  versions, platform strings, schema versions and aggregate counters — and **no** paths, no home
  directory, no hostname, no account name, no filenames, no hashes, no content. Verified by
  scanning the output for the account-name literal: zero hits. The preview-then-confirm gate
  works: without `--yes` the preview prints and **no file is created**.
- **`add --summary-out` (#86) honours its contract.** `privacy: "aggregate_counts_only"`,
  `local_only: true`, five integers, nothing else. Written atomically and confirmed on disk at
  mode `0600` — stricter than the `diagnostics-export` bundle, which lands at `0644`.
- **`archiv ui` degrades correctly when headless.** With no display available:
  `archiv ui: no graphical display is available; use a local desktop session`, exit 1 — a clear
  message rather than a traceback or a hang.
- **The `FULL`/`PARTIAL` support column** added to `archiv formats` (#90) is a real improvement
  in honesty over the previous round's output, which stated only the extraction method.
- **Format coverage confirmed in bulk:** DOCX (1,869 files), native PDF, XLSX, PPTX including a
  73.9 MB presentation, the ODF family, `.odb` (metadata only), InPage `.inp`, and WAV metadata
  all ingested without incident.

## 5. Performance snapshot (this hardware, clean run)

| Operation | Wall | Peak RSS | Notes |
|-----------|------|----------|-------|
| Full-corpus `add` (2,097 files, 498 MB) | 5m25s | 1.20 GB | 2,056 objects / 441,298 passages; OCR active |
| `rebuild-search-index` | 10.8 s | 424 MB | byte-identical output |
| `pytest` (286 tests) | 55.8 s | — | 1 pre-existing failure |
| `status --json` (incl. full integrity check) | 1.2 s | 217 MB | 2,056 objects + 2,348 evidence records verified |

Single machine, single pass, thermal state uncontrolled. Not a benchmark.

## 6. Suggested tracker items

1. `fix(ingestion)`: **scope `MAX_PAGES` to the OCR path it is documented for**, or add an
   opt-out and a distinct non-malformation error class. Update `docs/visual-ocr.md:105`, which
   still describes the cap as OCR-only. Highest priority — this is silent content loss on a
   legitimate document (§3.1).
2. `fix(ingestion)`: **persist failures.** Write a row with `status = 'failed'` and the exception
   text into the existing `ingestions.error` column, and surface path + reason in `add`'s output.
   Lights up `status`, `diagnostics-export`, and the already-written `failed_ingestions` query
   in one change (§3.2).
3. `fix(cli)`: count `degraded` from `processor_skipped` alone; surface partial-family
   classification, if wanted, under its own name (§3.3).
4. `fix(cli)`: reconcile the two `degraded` definitions, and either drop or formally deprecate
   the `skipped_unsupported` alias (§3.4).
5. `fix(ingestion)`: treat a native text layer below a per-page character threshold as absent, so
   scanner-watermark PDFs fall through to OCR (§2.3) — still the largest content-quality gap.
6. `fix(tests)`: rebuild the privacy guardrail on `git ls-files` and on the contributor's **home
   directory path** rather than the bare account-name token, and make it run on hosted runners
   instead of self-skipping (§2.4).
7. `feat(search)`: apply a per-source slot cap to `find`/`search` (§2.5).
8. `chore(cli)`: settle on one JSON convention — either give every command a `--json` flag or
   document which ones emit JSON unconditionally — and wrap the array-shaped payloads in an
   object with a top-level `schema_version` (§2.6).
9. `chore(ci)`: the CodeQL workflow has been failing at startup since 2026-07-30 for a reason
   already diagnosed as a repository-settings issue, not a code issue. Out of scope for this
   report; noted so it is not rediscovered.

## 7. Reproduction

```sh
git checkout 3e0703d
uv venv && uv pip install -e '.[dev]'
archiv doctor && ruff format --check . && ruff check . && pyright && pytest
export ARCHIV_HOME=~/archiv-home-clean                 # empty home on persistent disk, not /tmp
/usr/bin/time -v archiv add /path/to/corpus --json --summary-out summary.json > add.json
archiv status --json
archiv rebuild-search-index                        # compare index_sha256 with add.json
```

For §3.1, any PDF with more than 250 pages reproduces the rejection; for §3.3, any folder of
fully-extractable native PDFs reproduces the mislabelling.
