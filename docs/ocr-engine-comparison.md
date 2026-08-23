# OCR engine comparison decision

## Decision

Keep Archiv's production OCR defaults unchanged.

The hosted reference run supports language-aware benchmark routing, not an automatic production switch:

- known English: Tesseract `eng`;
- known Arabic: Tesseract `ara` as the low-cost baseline, with RapidOCR available only as an optional comparison/review engine;
- known Urdu: separately installed Tesseract `urd_naw` is the strongest measured candidate, but it remains opt-in until its training-data provenance is resolved and the result is repeated on the target Fedora machine with lawful private material;
- mixed-script and degraded material: no tested candidate is dependable enough for unattended indexing or transcription;
- Kraken: blocked because no specific printed-Urdu model with exact identity, hash and explicit evaluation terms was verified.

No confidence or accuracy threshold is introduced. OCR remains derived evidence, never overwrites native extraction, and does not change processing policy.

## Reference evidence

The final public-safe comparison ran on commit `501084df10df02dfa37f0fa96b8af0a3e60edaf4` in Fast Checks run `31113559997`. The exact matrix was materialized with network access and then rerun in Docker with `--network none`; the report records `denied-docker-network-none`.

Artifact `8972823016` has digest `sha256:9e739174aa1ce80caa29640c821e9f9cb96837cf2b98a05a111193291c36a694`.

Reference environment:

- Ubuntu 24.04 container on a GitHub-hosted Azure virtual machine;
- Intel Xeon Platinum 8370C, four visible CPUs;
- Python 3.12.3;
- Tesseract 5.3.4;
- RapidOCR 3.9.2, ONNX Runtime 1.27.0 and python-bidi 0.6.11;
- 13 Archiv-authored public fixtures and no private fixtures.

These are hosted reference measurements, not HP Victus/Fedora results and not universal OCR claims.

## Aggregate measurements

| Candidate | CER | WER | Reading-order error | Time | Peak RSS |
|---|---:|---:|---:|---:|---:|
| Tesseract `eng` | 83.1% | 99.1% | 135.0% | 2.041 s | 50,216 KiB |
| Tesseract `ara` | 56.6% | 100.9% | 140.0% | 1.356 s | 40,460 KiB |
| Tesseract `urd` | 49.4% | 87.6% | 140.0% | 1.338 s | 41,464 KiB |
| Tesseract `eng+ara+urd` | 48.7% | 83.2% | 135.0% | 3.189 s | 63,184 KiB |
| Tesseract `urd_naw` | 37.6% | 54.9% | 135.0% | 2.122 s | 54,092 KiB |
| Tesseract `eng+ara+urd_naw` | 40.4% | 62.8% | 130.0% | 3.949 s | 75,752 KiB |
| RapidOCR PP-OCRv5 Arabic mobile | 60.5% | 80.5% | 95.0% | 13.960 s | 586,408 KiB |

Aggregate scores combine deliberately different languages and degradation modes. They are useful for exposing broad failure and resource cost, not for selecting one universal engine.

## Route-level findings

| Material | Best measured accuracy | Product interpretation |
|---|---|---|
| Known English | Tesseract `eng` and RapidOCR both reached 0% CER/WER | Choose Tesseract `eng`; RapidOCR adds no accuracy here and has far higher aggregate resource cost. |
| Known Arabic | RapidOCR: 9.7% CER, 20.0% WER; Tesseract `ara`: 16.1% CER, 20.0% WER | Keep Tesseract `ara` as the baseline. RapidOCR's CER gain is real on this fixture but does not justify making a roughly 14-second, 586 MiB engine the default from this small corpus. |
| Known Urdu | Tesseract `urd_naw`: 2.4% CER, 12.5% WER; standard `urd`: 13.4% CER, 31.3% WER | `urd_naw` is the strongest Urdu benchmark candidate, subject to provenance and target-machine confirmation. RapidOCR was poor at 54.9% CER and 62.5% WER. |
| Unknown or mixed script | Best result was `urd_naw`: 61.0% CER, 83.3% WER | Do not automate. Preserve OCR as reviewable evidence and require operator review or better routing/layout analysis. |
| Degraded material | Best CER was `eng+ara+urd_naw`: 46.1%; best WER was `urd_naw`: 64.5% | Do not automate. High omissions, substitutions and reading-order errors make every tested route unsafe for unattended transcription. |

## Resource and model evidence

Tesseract `urd_naw` used a 7,747,460-byte model:

- repository: `tesseract-ocr/tessdata_contrib`;
- commit: `1b7ada6f9ed0e165f06b3212500e1433fdf4dfc7`;
- path: `urd_naw/best/urd_naw.traineddata`;
- Git blob SHA-1: `cb79560e7c97ea56082d1e285ffa3dcc319b1113`;
- SHA-256: `1dc7a5eae96b7fc1834332681be8cc3d9cdab4c93a8f03ba9c0837106abbf207`;
- repository licence: Apache-2.0;
- unresolved point: the upstream README links a training dataset but does not enumerate source rights.

RapidOCR's selected model cache was 18,538,954 bytes, while the measured RapidOCR package and ONNX Runtime trees brought the inspected footprint to about 108.7 MB before other Python dependencies. Exact selected model hashes:

- `PP-OCRv6_det_small.onnx`: `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f`;
- `arabic_PP-OCRv5_rec_mobile.onnx`: `c1192e632d0baa9146ae5b756a0e635e3dc63c1733737ebfd1629e87144e9295`;
- `ch_ppocr_mobile_v2.0_cls_mobile.onnx`: `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c`.

RapidOCR code is Apache-2.0. Its project notice attributes OCR model copyright to Baidu; Archiv neither bundles nor redistributes the weights.

## Remaining target validation

The HP Victus Fedora run remains unresolved. Run:

```bash
bash tools/run-ocr-engine-comparison.sh "$HOME/archiv-ocr-benchmark" /optional/private-corpus
```

The command installs the Fedora runtime packages, verifies the exact `urd_naw` Git blob, materializes optional models once, then reruns the same matrix inside a bubblewrap network namespace. A lawful private corpus is needed before adopting any specialist route for real documents.

## Scope left in Issue #54

Issue #54 stays open for target-machine/private-corpus validation and later visual-recovery work: layout and reading-order engines, additional renderers, optional VLM comparison, OCRmyPDF and related provenance-preserving extensions.
