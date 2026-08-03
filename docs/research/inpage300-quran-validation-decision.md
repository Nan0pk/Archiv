# InPage300 Quran validation decision

## Current decision

Do not register `.inp` or connect the InPage300 research decoder to production
ingestion merely because a Quran comparison produces readable or high-scoring text.

## Evidence required from this slice

- both pinned public InPage300 files are extracted twice with stable private hashes;
- both reference sources pass complete 114-surah and 6,236-ayah structural validation;
- Juz 29 covers exactly 67:1 through 77:50 and Juz 30 exactly 78:1 through 114:6;
- whole-text and in-order verse coverage are reported for every predefined
  normalization mode;
- no decoded or reference text appears in logs, artifacts or Git history;
- source hashes, licences, workflow head and artifact hash are recorded.

## Possible outcomes

### Complete in-order coverage against both references

This would support a narrow claim that the bounded InPage300 algorithm recovered the
expected Quran verse sequence from these two exact files. It would still not establish
layout, page/frame locators, all InPage300 versions, unrelated-document handling or
lawful fixture redistribution.

### Complete coverage only after a specific normalization

The normalization must be justified character by character at the category level.
Human review remains required for punctuation, numerals, Bismillah handling, direction,
extra content and omissions. The strongest score is not automatically authoritative.

### Partial or conflicting coverage

Record the exact sanitized metrics and stop. Do not tune thresholds, strip additional
characters or choose a reference merely to improve the result. Any follow-up must begin
from an independently stated hypothesis and new regression tests.

Issue #38 remains open under every outcome in this slice.
