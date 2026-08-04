# OCR engine comparison decision

## Current status

Implementation and public-safe validation are in progress on Issue #54. This document is intentionally not a marketing comparison and will be replaced with exact hosted-runner measurements before merge.

## Candidate scope

- Tesseract `eng`, `ara`, `urd`, `eng+ara+urd`;
- separately installed `urd_naw` and `eng+ara+urd_naw` at a fixed upstream commit;
- optional RapidOCR 3.9.2 / ONNX Runtime 1.27.0 with PP-OCRv5 Arabic mobile recognition;
- Kraken recorded as blocked until a specific lawful printed-Urdu model is identified.

## Decision gate

Normal ingestion defaults remain unchanged until the same lawful corpus produces inspectable measurements. The final decision will consider accuracy, reading order, CPU time, peak RSS, installation/model footprint, offline behavior, licence clarity and implementation burden.

Possible valid outcomes include retaining Tesseract, routing known Urdu to a specialist, keeping another engine benchmark-only, or concluding that no tested candidate is dependable for Urdu transcription.

## Target hardware

No HP Victus result is claimed here. Hosted CI measurements are reference-only. The repository provides one command and a private-corpus path for the actual Fedora run.
