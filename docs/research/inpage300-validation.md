# InPage300 validation plan

## Current evidence

PR #49 measured `juz_29.inp` as CFB v3 with a 410,578-byte `InPage300` stream whose
SHA-256 is `644ce2e08032d3ad914366dfce0561ca9e431a17429a538e1a49b97854fbf199`.
An aligned aggregate scan found 205,289 16-bit units, including 45,511
Arabic/Perso-Arabic units and a longest permitted run of 54,655 units. This is strong
structural evidence for directly encoded Unicode text, but not proof of textual
correctness or reading order.

## Independent extraction

The research extractor scans aligned UTF-16LE units using explicit ranges for:

- Arabic, Arabic Supplement, Arabic Extended-A and presentation forms;
- printable ASCII;
- tabs and line separators;
- ZWNJ/ZWJ and explicit bidi controls.

A run is retained only when it meets the configured minimum and contains at least one
Arabic/Perso-Arabic unit. The result records raw and NFC text hashes, counts,
replacement characters, unpaired surrogates, controls, lines, paragraphs, rejected
regions and rejected bytes. No text enters public logs or artifacts.

## Ground-truth gate

For both `juz_29.inp` and `juz_30.inp`, compare the private extracted text with an
independently pinned, lawfully usable Quran corpus. Record the provider, exact commit,
licence, file hash and corpus segmentation. Report these modes separately:

1. exact NFC;
2. whitespace-normalized;
3. diacritic-insensitive;
4. verse-symbol-normalized.

For each mode publish only normalized hashes, lengths, matching-character count,
SequenceMatcher ratio, length delta and exact-match status. Do not silently stack
normalizations until a match appears. Human review must still verify verse order,
numerals, punctuation and direction.

## Readiness threshold

InPage300 remains not ready until two samples reproduce deterministically, match
independent ground truth at a justified level, reject malformed inputs, and have a
bounded family/version rule. Layout remains out of scope.
