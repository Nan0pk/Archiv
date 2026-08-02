# InPage300 validation

## Measured extraction

Evidence run `30771593543` verified and extracted two pinned public candidates
deterministically without publishing document text.

| Candidate | Stream bytes | Text code points | Arabic/Perso-Arabic | Lines | Accepted runs | NFC text SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| `juz_29.inp` | 410,578 | 54,688 | 44,910 | 4 | 3 | `633666b717facf00ce8ebe35c33dccdeb5a334f43f726b83bc71a512d462599b` |
| `juz_30.inp` | 543,086 | 68,020 | 54,508 | 55 | 55 | `6fe901aed206d7cfd90627cce91c8967e1c7fff35b08b23af69ad71575616ebe` |

For `juz_29.inp`, the bounded extractor reproduced PR #49's aggregate stream
measurements exactly: 205,289 aligned units, 45,511 Arabic/Perso-Arabic units, 16,636
printable ASCII units, 65,862 zero units and a longest allowed run of 54,655 units.
Both files produced stable raw and NFC hashes across repeated runs, with no replacement
characters emitted.

## Interpretation

This is direct evidence that both `InPage300` streams contain large, reproducibly
extractable aligned Unicode/Perso-Arabic regions. It is not evidence that all retained
regions are document text, that omitted regions are non-text, or that the resulting
order and punctuation are correct.

The very different run and line counts—three accepted regions for `juz_29` and 55 for
`juz_30`—also show that simple run boundaries cannot be promoted into page, paragraph,
story or frame locators.

## Ground-truth gate

Compare both private extracted texts with an independently pinned, lawfully usable
Quran corpus. Record provider, exact commit, licence, file hash and Juz segmentation.
Report these modes independently:

1. exact NFC;
2. whitespace-normalized;
3. diacritic-insensitive;
4. verse-symbol-normalized.

For each mode publish only normalized hashes, lengths, matching-character count,
matching ratio, length delta and exact-match status. Do not stack normalizations until
a favorable match appears. Human review must verify verse order, numerals, punctuation,
direction and any extra or missing material.

## Readiness threshold

InPage300 remains not ready until both files match independent ground truth at a
justified level, logical reading order is verified, normalization differences are
explained, malformed inputs fail closed and the family/version rule is bounded. Layout
support remains unproven.
