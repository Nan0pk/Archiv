# OCR language benchmark

Archiv includes a small operator-run benchmark for measuring installed Tesseract language configurations before changing the production OCR default.

```bash
archiv benchmark-ocr --output "$HOME/archiv-ocr-benchmark"
```

See the [measured multilingual OCR baseline](ocr-benchmark-report.md) for the first real Tesseract results and their limitations.

The command creates four synthetic PNG fixtures:

- clean English;
- clean Arabic Naskh;
- clean Urdu Nastaliq;
- a mildly degraded mixed Urdu/English scan.

The phrases are authored for Archiv. Fonts are lawful local inputs: the benchmark records their paths and SHA-256 hashes but never copies them into the repository or benchmark report.

## Measurements

For each installed candidate, Archiv records:

- normalized hypothesis text;
- character error rate and word error rate;
- substitution, deletion and insertion counts;
- punctuation and numeral error rates;
- omitted lines and inserted characters;
- wall-clock time;
- peak process RSS when GNU `time` is available;
- Tesseract version and executable hash;
- the exact traineddata path, byte size and SHA-256 for selected language models;
- available language models;
- fixture image, font and corpus-manifest hashes.

Model licence status is recorded as requiring operator verification. Archiv does not infer redistribution or usage terms from an installed filename.

The default candidate matrix is:

- `eng`;
- `ara`;
- `urd`;
- `eng+ara+urd` when all three are installed;
- `urd_naw` and `eng+ara+urd_naw` only when that contributed model is separately installed.

A specific matrix can be selected explicitly:

```bash
archiv benchmark-ocr \
  --output "$HOME/archiv-ocr-benchmark" \
  --candidates eng,ara,urd,eng+ara+urd
```

The output directory contains generated fixtures, `corpus.json`, and `report.json`. The reported `recommended_candidate` is only the lowest measured CER/WER result for that exact synthetic corpus and machine. It is not a universal accuracy claim and does not automatically change Archiv's production OCR configuration.

## Font selection

Archiv searches common system font locations for Noto Sans, Noto Naskh Arabic and Noto Nastaliq Urdu. Explicit paths can be supplied when distributions use different locations:

```bash
export ARCHIV_OCR_BENCHMARK_FONT_ENG=/path/to/NotoSans-Regular.ttf
export ARCHIV_OCR_BENCHMARK_FONT_ARA=/path/to/NotoNaskhArabic-Regular.ttf
export ARCHIV_OCR_BENCHMARK_FONT_URD=/path/to/NotoNastaliqUrdu-Regular.ttf
```

Pillow must have complex-text layout support for correct Arabic/Urdu shaping. A missing font, language model or OCR executable fails the benchmark clearly instead of producing a partial recommendation.

## Boundaries

This benchmark is deliberately small. It does not establish production quality for handwriting, tables, multi-column reading order, camera distortion, private documents or arbitrary fonts. Issue #54 remains open for a larger lawful corpus and comparison with `urd_naw`, Paddle/RapidOCR and Kraken before any broader engine decision is made.
