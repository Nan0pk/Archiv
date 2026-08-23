# OCR engine comparison

`archiv benchmark-ocr` extends the original Tesseract language benchmark without creating a second platform. It generates one lawful public corpus, optionally appends an explicitly local/private corpus, and normalizes the fixed candidate set into the same report.

The normal production OCR path is unchanged. Benchmark output remains derived evidence and cannot overwrite native extraction or alter ingestion policy.

## Public-safe comparison

```bash
archiv benchmark-ocr \
  --output "$HOME/archiv-ocr-comparison" \
  --engines tesseract,rapidocr,kraken \
  --candidates eng,ara,urd,eng+ara+urd,urd_naw,eng+ara+urd_naw
```

Tesseract candidates that are not installed remain visible as `unavailable`. RapidOCR remains an optional benchmark dependency. Kraken is recorded as `blocked` until a specific printed-Urdu model has exact identity, hash and explicit terms; the benchmark does not download an unspecified model.

The output directory contains:

- `corpus.json`: full local fixture and generation evidence;
- `report.json`: full local hypotheses, metrics, paths and engine/model evidence;
- `summary.md`: human-readable results;
- `shareable-summary.json`: aggregate-only output with no private text, crops, filenames or fixture identifiers;
- generated public fixtures and optional engine model caches.

## Corpus

The generated corpus exercises each important failure mode more than once where practical:

- English;
- Arabic Naskh;
- Urdu Nastaliq rendered with two distinct local fonts;
- mixed Urdu and English;
- Arabic and Western numerals;
- punctuation;
- multiple lines;
- two columns and reading order;
- labelled form fields;
- clean pages;
- blur, rotation and reduced contrast;
- phone-photograph-like perspective distortion.

Every public phrase is authored for Archiv. The generator records the exact local font path/hash, Pillow generation settings, transformations and image hash. Fonts and generated images are not committed.

A second Nastaliq font is required rather than silently pretending one font represents Urdu typography. On Fedora, the target command installs both Noto Nastaliq Urdu and Nafees Nastaleeq.

## Local/private corpus

Real InPage-origin renders and private documents stay local. Supply a directory containing `manifest.json`:

```json
{
  "schema_version": "1",
  "fixtures": [
    {
      "fixture_id": "local-inpage-page-01",
      "language": "Urdu Nastaliq",
      "ground_truth": "operator-verified text",
      "expected_lines": ["operator-verified text"],
      "image_path": "pages/page-01.png",
      "tags": ["urdu", "inpage-origin", "clean"],
      "source_kind": "private_inpage_render",
      "lawful_basis": "operator owns or is authorized to evaluate the document",
      "generation_method": "local InPage print/export rendered to PNG"
    }
  ]
}
```

Run it with:

```bash
archiv benchmark-ocr \
  --output "$HOME/archiv-ocr-comparison" \
  --engines tesseract,rapidocr,kraken \
  --private-corpus /path/to/private-corpus
```

Manifest image paths must be relative, stay inside the corpus directory and be no larger than the normal OCR input bound. The full local report may contain ground truth and hypotheses. `shareable-summary.json` contains aggregate measurements only.

## Measurements

For each successful candidate, Archiv records:

- CER and WER;
- character and word substitutions, deletions and insertions;
- omitted and inserted lines;
- inserted or hallucinated characters;
- punctuation and numeral accuracy;
- reading-order errors;
- fixture-level and aggregate results;
- CPU wall time and peak RSS when GNU `time` is available;
- engine executable/package identity and footprint;
- model identity, file hashes, sizes and total footprint;
- licence/provenance evidence and warnings;
- coordinates and confidence when the engine exposes them.

Failed, unavailable and blocked candidates remain in the ranking ledger. They are never silently removed to make the result look better.

No accuracy threshold is invented. The report names the best measured result for the exact corpus, but automatic search-indexing suitability remains an explicit product decision based on the observed failures and source risk.

## Candidate evidence

### Tesseract

Standard `eng`, `ara`, `urd` and combined configurations use locally installed traineddata and are hashed at run time.

`urd_naw` is evaluated only when separately installed. The fixed upstream identity is:

- repository: `tesseract-ocr/tessdata_contrib`;
- commit: `1b7ada6f9ed0e165f06b3212500e1433fdf4dfc7`;
- path: `urd_naw/best/urd_naw.traineddata`;
- Git blob SHA-1: `cb79560e7c97ea56082d1e285ffa3dcc319b1113`;
- repository licence: Apache-2.0.

The upstream README links a training dataset but does not enumerate the rights of every source. Archiv therefore does not bundle the model or describe it as production-proven. The local run records its actual SHA-256 and size.

### RapidOCR

The bounded optional candidate is RapidOCR 3.9.2 with ONNX Runtime 1.27.0 on CPU, using the PP-OCRv5 Arabic mobile recognizer. The Arabic-script recognizer is the relevant family for Urdu/Arabic comparison; PP-OCRv6 is not substituted silently.

RapidOCR code is Apache-2.0. Its project notice says OCR model copyright is held by Baidu. Archiv downloads no weight during normal installation, does not redistribute weights, hashes materialized model files and records the notice in the report.

### Kraken

Kraken itself is not enough. A benchmark requires one specific printed-Urdu model with a verifiable source, exact model identity/hash and explicit terms. Until that exists, Kraken is an inspectable blocked candidate with no downloaded weights.

## Target Fedora command

From a source checkout, the same corpus and full matrix run through:

```bash
bash tools/run-ocr-engine-comparison.sh "$HOME/archiv-ocr-benchmark" /optional/private-corpus
```

The script installs the Fedora OCR/font prerequisites, creates an isolated benchmark environment, materializes the exact `urd_naw` Git blob, runs once to populate optional RapidOCR models, then reruns the comparison inside a bubblewrap network namespace. The final evidence comes from the network-denied rerun.

Hosted-runner results are reference-only. A report is target-hardware evidence only after its environment is checked against the HP Victus Fedora machine.
