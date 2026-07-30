# Test fixtures

Only synthetic, generated, public-domain, or explicitly redistributable fixtures may be committed here.

Every binary fixture added later must have:

- a source or generator;
- a licence or provenance note;
- a SHA-256 digest in a manifest;
- unique expected markers;
- no personal, private, classified, customer, or proprietary information.

## Representative corpus

`representative-corpus/` stores the committed manifest, expected locations, and provenance for Archiv's first cross-format corpus. The binary files themselves are generated into ignored build or temporary directories, keeping the public Git history small while preserving exact reproducibility.

Generate the corpus with:

```bash
python scripts/generate_fixture_corpus.py
```

The default output is `build/fixtures/representative-corpus/`. Tests regenerate the corpus independently and require every byte, SHA-256 digest, marker, location, Office package member, image dimension, audio property, and malformed rejection sample to match the committed descriptors.
