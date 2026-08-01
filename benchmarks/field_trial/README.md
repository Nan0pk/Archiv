# Archiv public field-trial benchmark

This directory defines the first reproducible, public-safe real-work baseline for Archiv.

`benchmark.json` contains:

- 12 synthetic documents generated at runtime in TXT, Markdown, PDF, DOCX, XLSX, and PPTX;
- 22 questions covering fact retrieval, synthesis, superseded versions, contradictions, unresolved actions, dates, numbers, duplicates, lexical overlap, missing evidence, broad status, risks, unsupported assumptions, source-specific requests, and multi-citation answers;
- machine-readable expected sources, required facts, allowed insufficient-evidence outcomes, contradiction expectations, forbidden claims, and deterministic fake-model responses.

Run it from a development checkout:

```bash
python scripts/run_field_trial.py --public --output field-trial-artifacts
```

Use `--render-report` only where LibreOffice and Poppler are installed. The committed definition contains no private documents, filenames, paths, prompts, model credentials, or user data.
