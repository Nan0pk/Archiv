# Measured multilingual OCR baseline

## Decision

Retain Tesseract with the combined `eng+ara+urd` language configuration as Archiv's small local baseline when the input language is unknown or mixed.

This is a routing decision, not an accuracy claim. On the four-fixture synthetic corpus, the combined configuration had the best aggregate result but still produced **35.1% character error rate** and **41.7% word error rate**. Clean Urdu and degraded mixed-script results remain materially error-prone. Archiv must continue to label the output as derived `visual_ocr` evidence and preserve the source image for inspection.

When the script is known in advance, explicit language selection can improve character accuracy: `ara` was best for the clean Arabic fixture and `urd` was best for the clean Urdu fixture. The benchmark does not automatically change `ARCHIV_OCR_LANGUAGES`.

## Measurement environment

- Date: 2026-08-04
- GitHub Actions run: `30929775856`
- Branch head: `cc06ea9db6a9af1205f53dfae10c9878db6e2371`
- Runner: Ubuntu 24.04 hosted runner
- Python: 3.12
- OCR engine: Tesseract 5.3.4
- Candidates: `eng`, `ara`, `urd`, `eng+ara+urd`
- Page segmentation mode: `6`
- OpenMP worker limit: 2
- Generated report SHA-256: `79cb6e10555275f237d2eb5912ed3177f423cbbcf2934147b9a5571addb96f0b`

The benchmark installed broad Noto font packages to provide lawful local rendering inputs. The package transaction reported 412 MB of additional disk use, dominated by the font packages. That figure is **not** Archiv's normal OCR runtime or trained-model footprint.

## Corpus

The benchmark generated four local PNG fixtures from Archiv-authored phrases:

1. clean English;
2. clean Arabic Naskh;
3. clean Urdu Nastaliq;
4. mildly rotated, blurred and contrast-reduced mixed Urdu/English text.

The generator records every fixture image hash and the exact local font path and hash. Fonts and generated images are not committed to the repository.

This corpus is deliberately small. It does not represent handwriting, tables, multi-column reading order, severe camera distortion, private documents or arbitrary fonts.

## Aggregate results

| Candidate | CER | WER | Total wall time | Peak RSS |
|---|---:|---:|---:|---:|
| `eng` | 67.6% | 83.3% | 0.553 s | 41,124 KiB |
| `ara` | 68.2% | 95.8% | 0.325 s | 30,428 KiB |
| `urd` | 61.5% | 83.3% | 0.322 s | 31,496 KiB |
| **`eng+ara+urd`** | **35.1%** | **41.7%** | **0.796 s** | **52,348 KiB** |

The timings are hosted-runner reference measurements, not predictions for the target Fedora laptop.

## Script-specific findings

| Fixture | Best relevant candidate | CER | WER | Important failure |
|---|---|---:|---:|---|
| Clean English | `eng` or `eng+ara+urd` | 0.0% | 0.0% | None on this fixture |
| Clean Arabic | `ara` | 16.1% | 20.0% | Numeral and punctuation errors remained |
| Clean Urdu | `urd` | 23.1% | 57.1% | Numerals remained wrong; one inserted character |
| Clean Urdu | `eng+ara+urd` | 38.5% | 42.9% | Better word recovery but worse character accuracy |
| Degraded mixed text | `eng+ara+urd` | 73.8% | 85.7% | Too inaccurate for dependable transcription |

No tested configuration omitted an entire line, but several produced inserted characters. Numerals and punctuation were a recurring weak point, especially when the selected language did not match the fixture.

## Exact trained-model evidence

The Ubuntu packages supplied three traineddata files. The benchmark resolved the Tesseract data directory, hashed the files and recorded their sizes:

| Language | Bytes | SHA-256 |
|---|---:|---|
| `ara` | 1,432,056 | `e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729` |
| `eng` | 4,113,088 | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| `urd` | 1,398,718 | `62e8250ce2a994106e313a82e26a516a39e2cf159d0ce3c5b5008387fd0d555f` |

Combined traineddata size: **6,943,862 bytes**.

The report records model licence status as `operator_verification_required`. Archiv does not infer redistribution or usage terms from a package name or installed filename.

## Product implications

- Keep `eng+ara+urd` as the practical baseline for unknown or mixed English/Arabic/Urdu input.
- Preserve explicit language selection for known-script workloads because `ara` and `urd` can improve character accuracy on their matching clean fixtures.
- Do not describe standard Tesseract Urdu as accurate transcription for Nastaliq documents.
- Treat numerals and punctuation as known weak evidence requiring source inspection.
- Do not promote OCR confidence to proof of correctness.
- Keep native extraction and OCR output separate and retain exact visual-region citations.

## Remaining work

Issue #54 remains open. The next evidence-producing work is:

1. expand to a lawful real-world corpus with multiple fonts, scans, phone photographs, forms and InPage-origin renders;
2. verify the provenance and terms of `urd_naw`, then measure it on exactly the same corpus;
3. compare a bounded CPU PaddleOCR/RapidOCR candidate;
4. compare Kraken only when a lawful printed-Urdu model is available;
5. measure the same candidates on the target Fedora hardware;
6. evaluate layout and reading order only after text-recognition baselines are justified.

No universal engine, format or accuracy claim is supported by this benchmark.
