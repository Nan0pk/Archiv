# InPage300 Quran ground-truth validation

Status: research only. Native `.inp` remains unsupported.

## Purpose

The two public `InPage300` candidates are labelled Juz 29 and Juz 30 and already
produce deterministic private text hashes. This slice tests whether those extracted
characters correspond to the expected Quran verses and order. It does not validate
InPage layout, version coverage or production ingestion.

## References

The evidence runner accepts two independently identified references:

1. `amrayn/quran-text` at commit
   `d1868b249234f536c6048da69c272efc91ce44b4`, using the MIT-licensed
   `quran-full-tashkeel.json` Git blob
   `ceccc426c01a7eef87383608efd8412064ad5cb0`;
2. an ephemeral official Tanzil Quran XML download, version 1.1, governed by the
   Tanzil Creative Commons Attribution 3.0 notice and terms. Its exact downloaded
   SHA-256 must be recorded by the evidence run, and no reference text is retained.

Juz 29 is fixed to verses 67:1 through 77:50, 431 verses. Juz 30 is fixed to verses
78:1 through 114:6, 564 verses. The parser independently validates all 114 surahs,
all 6,236 verses and the canonical per-surah verse counts before comparison.

## Comparison modes

Each source is compared without selecting a favorable result after the fact:

- raw;
- NFC;
- whitespace-normalized;
- diacritic-insensitive;
- verse-symbol-normalized.

Two measurements are reported for every mode:

- whole-text similarity and exact-match status;
- sequential verse coverage, including matched verses, contiguous prefix, first
  unmatched verse and last matched verse.

The sequence measurement tolerates extra headings or page material but never reorders
verses. All output is sanitized counts, ratios and hashes; source, reference and decoded
text remain private.

## Decision gate

A strong InPage300 text result requires both Juz files to show complete in-order verse
coverage against independent references under a justified normalization, with any
whole-text differences explained by bounded headers, verse markers, Bismillah handling
or whitespace. Human review is still required for punctuation, numerals, direction,
omissions and extra material.

Even a complete Quran match does not register `.inp`, prove page/story/frame locators,
identify all InPage300 versions or close issue #38.
