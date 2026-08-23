# Real-world corpus field notes — 2026-08-23 (nascent, informal)

> **Status: nascent / informal.** This is one ad hoc test run against a real-world
> corpus, not the frozen, CI-measured public benchmark in
> [`field-trial-report.md`](field-trial-report.md). Numbers below come from a
> single machine, a single pass, and a test corpus that has its own quality
> issues (noted below). Treat this as a bug/todo list to triage, not a
> certified result.

**Tested:** `0.1.0a6` @ `32a6a94`, installed from source (`pip install -e '.[dev]'`)
**Platform:** Fedora, Python 3.14.7, tesseract 5.5.3 (eng/ara/urd), bubblewrap present, LibreOffice present
**Corpus:** 2,098 real-world files (DOCX, PDF incl. scans, XLSX, PPTX, ODF family, InPage `.inp`, photos, RTL text in Urdu/Pashto/Dari/Arabic, WAV, plus deliberately malformed/unsupported files)
**Model backend:** local Ollama loopback — `granite4:micro`, `qwen3.5:4b-q4_K_M`

## Summary

Core architecture holds up at scale: immutable ingestion, fail-closed validation,
FTS retrieval with verified citations, source relocation, backup/restore
round-trip, sandboxed OCR, and the privacy boundary all worked correctly on
2,098 files / 588k segments. Upstream suite was 248/250 before this pass.

## Todo

- [x] **High (bug) — bwrap sandbox breaks visual OCR whenever `ARCHIV_HOME` is
      under `/tmp`.** `--tmpfs /tmp` in `visual_ocr.py::_run` masked the
      `--ro-bind / /` for both the canonical original (image OCR) and the
      source PDF (page-render OCR) whenever they resolved under `/tmp`. Fixed
      in this change by re-exposing the relevant parent directory read-only
      after the tmpfs; covered by a new regression test that runs the real
      `bwrap` + `tesseract` binaries with `ARCHIV_HOME` under `/tmp`
      (previously every OCR test forced `ARCHIV_OCR_SANDBOX=off`, so this path
      had zero coverage).
- [x] **Medium (bug) — repo fails its own privacy test at HEAD.** The literal
      username `victus` (this test machine's account name) was committed in
      three places, not just the one originally spotted:
      `tools/run-ocr-engine-comparison.sh` (the actual default), plus two docs
      that copy-pasted the same example command —
      `docs/ocr-engine-comparison.md` and `docs/ocr-benchmark.md`. All three
      now use a generic `$HOME/archiv-ocr-benchmark` default, matching the
      naming already used elsewhere in the README. `test_no_private_paths_or_secrets_in_tracked_files`
      passes again.
- [ ] **High (quality) — `ask` can emit a fabricated claim that still passes
      "Verified Sources".** Citation-existence validation checks that
      citations are real and hash-match, not that the cited passage actually
      supports the claim. Reproduced with a false-premise probe; the sibling
      refusal probe (fabricated agreement) was correctly refused, so this is
      model/prompt-sensitive rather than systematic. Needs a design decision
      (entailment spot-check or quote-substring requirement per claim), not a
      one-line fix — left open.
- [ ] **Medium (usability) — `report` fails wholesale on small local models.**
      `granite4:micro` failed closed with every inline citation marker judged
      misplaced. Fail-closed behavior is correct; the citation-format contract
      is too brittle for small models. Also: evidence dir doesn't persist the
      raw model response for `report` the way `ask` does, making failures hard
      to debug after the fact.
- [ ] **Medium (retrieval) — bm25 crowding on the test corpus.** Two ~24MB
      concatenated "digest" files in the *test data*, not the tool, dominate
      generic-term rankings and push true sources out of top-100. Best fixed
      by repairing the test corpus (exclude/relabel the digest files, see the
      test-data quality notes from this run) rather than the retrieval engine
      itself, though a near-duplicate-aware ranking signal would help
      generally.
- [ ] **Low (bugs/UX):**
  - `archiv verify <ask-run-id>` raises a raw `FileNotFoundError` traceback
    instead of a clean error — `verify` only supports task runs.
  - `archiv restore` requires `--home` even though its help text says it
    defaults to `ARCHIV_HOME`.
  - `archiv ingest` / `archiv search` have no `--json`, despite the README
    saying "most commands accept `--json`".
  - `archiv add <dir>` with only invalid-but-supported-extension files aborts
    with one generic line instead of per-file diagnostics.
  - `benchmark-ocr`'s undocumented env vars (`ARCHIV_OCR_BENCHMARK_FONT_ENG` /
    `_ARA` / `FONTS_URD`) aren't mentioned in the README/docs.
  - No documented guidance on which local models fit the default 120s `ask`
    timeout — `granite4:micro` does (~96–106s), `qwen3.5:4b-q4_K_M` does not
    (>420s even at `timeout=420`).

## What this run measured (unchanged by this PR)

- Full ingest: 2,098 files → 1,970 new, 93 content-duplicates, 35 unsupported,
  zero processing failures, 4m52s wall.
- Format coverage, fail-closed edge cases, citation chain, privacy boundary,
  backup/restore, and atomic index rebuild all verified correct.
- Retrieval: 10/20 manifest questions retrieved their expected source via bare
  `find`; most misses trace to test-corpus defects (duplicate digest files,
  facts that only exist in filenames, one manifest entry expecting a `.doc`
  source) rather than tool bugs.
