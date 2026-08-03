# InPage300 Quran ground-truth validation

Status: completed research slice with a failed validation result. Native `.inp`
remains unsupported.

## Purpose

The two public `InPage300` candidates labelled Juz 29 and Juz 30 produce
repeatable private text hashes. This slice tested whether the current bounded,
aligned UTF-16LE research extraction recovers the expected Quran text and
logical verse order. It did not test layout, page, frame or story recovery.

## Verified sources

The native candidates came from `ShakesVision/html-experiments` pinned at
`1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a`. Their fixture bytes were used only
inside an isolated temporary evidence job and were not committed or uploaded.

Two independently identified Unicode references were used:

1. `amrayn/quran-text` commit
   `d1868b249234f536c6048da69c272efc91ce44b4`, path
   `quran-full-tashkeel.json`, Git blob
   `ceccc426c01a7eef87383608efd8412064ad5cb0`, SHA-256
   `382a7341300602ec8b366316d4bbe2a44955c2bf984d395bdd82dae6110b6715`,
   MIT licence;
2. Tanzil Quran XML version 1.1, SHA-256
   `203f0f1bf3158b1e5be4ab9f8f6870e570aab6d9a626fe6192a70b75d4afe0fd`,
   under the Tanzil Creative Commons Attribution 3.0 notice and terms.

Both parsers independently require all 114 surahs, all 6,236 ayahs and the
canonical per-surah verse counts. Juz 29 is fixed to 67:1 through 77:50, 431
verses. Juz 30 is fixed to 78:1 through 114:6, 564 verses.

## Comparison contract

The four success-eligible modes are independent:

- exact NFC;
- whitespace-normalized;
- diacritic-insensitive;
- verse-symbol-normalized.

Raw comparison and Arabic-letters-only comparison are diagnostics and cannot
satisfy the gate. No result is selected merely because it scores highest.

Whole-text evidence records normalized hashes, lengths, positional matching,
length delta, punctuation and numeral differences, and insertion, deletion and
substitution counts. For these large texts the operation counts are explicitly
labelled `nonminimal_position_aligned`; they are bounded diagnostics, not a
minimal Levenshtein edit script. Sequential verse matching is bounded,
left-to-right and monotonic. It does not sort, reverse, drop unmatched margins or
search a different Juz.

The exact sanitized results are stored in
`inpage300-quran-ground-truth-measurements.json`. They were generated at head
`fedd80f3b3e80c5bcc7240f4e05a949c1faeac73` by workflow run `30832066360`.
The artifact ZIP digest is
`sha256:16163f61bd5fb2fd3cb604ad09a67a94b7098bc9f630109c7fbb358d8c63c96d`;
the contained sanitized JSON SHA-256 is
`81d7a724df1428e6d8cb97ce6d784c07da740cf29c6f7883ce977bbaac5066b0`.

## Direct measurements

The extraction itself repeated deterministically:

- Juz 29 candidate: 54,688 code points, 44,910 Arabic/Perso-Arabic code
  points, private NFC hash
  `633666b717facf00ce8ebe35c33dccdeb5a334f43f726b83bc71a512d462599b`;
- Juz 30 candidate: 68,020 code points, 54,508 Arabic/Perso-Arabic code
  points, private NFC hash
  `6fe901aed206d7cfd90627cce91c8967e1c7fff35b08b23af69ad71575616ebe`.

The correctness gate failed for every file/reference pair:

| Candidate | Reference | Exact NFC | Whitespace | No diacritics | Verse symbols |
| --- | --- | ---: | ---: | ---: | ---: |
| Juz 29 | Tanzil | 5/431 | 6/431 | 14/431 | 6/431 |
| Juz 29 | amrayn | 3/431 | 3/431 | 5/431 | 3/431 |
| Juz 30 | Tanzil | 2/564 | 2/564 | 33/564 | 2/564 |
| Juz 30 | amrayn | 0/564 | 0/564 | 7/564 | 0/564 |

Every primary comparison had zero contiguous opening verses. The first expected
verse was unmatched in every case. No primary mode produced complete in-order
coverage. The Arabic-letters-only diagnostic improved isolated matches to
19-22 verses for Juz 29 and 34-38 for Juz 30, but still had zero contiguous
opening verses and is deliberately ineligible for success.

The extracted normalized lengths were substantially larger than the references.
For example, Juz 29 exact NFC was 54,685 characters versus 23,912 in Tanzil;
Juz 30 exact NFC was 68,016 versus 20,826. The same comparisons recorded 909
versus 927 numeral-count differences and 236 versus 645 punctuation-count
differences respectively. These are incompatible with a narrow claim of correct,
complete logical Quran extraction.

## Adversarial checks

Tests establish that a favorable result cannot be manufactured by:

- using Arabic-letters-only stripping as a success mode;
- accepting reversed verse order;
- sorting verses;
- comparing against the wrong Juz;
- hiding unmatched prefixes or suffixes;
- merging normalization modes;
- silently reporting positional counts as a minimal edit script;
- allowing late XML entity or document-type declarations;
- emitting non-ASCII reference or extracted text in sanitized evidence.

## Human-verification boundary

No qualified Urdu/Arabic human review was performed. That is recorded as an
external blocker, not as a passed check. A future review must use private local
output and independently verify:

- beginning and ending verses;
- verse order and missing or duplicated passages;
- punctuation and Arabic numerals;
- diacritics, joining controls and bidi controls;
- unexpected English or metadata;
- whether the extraction order is logical reading order.

Because automated coverage already fails at the opening verse, human review
cannot convert this extraction algorithm into a validated parser; it can only
help classify the failure.

## Engineering decision

The current aligned UTF-16LE extraction hypothesis is not validated. The measured
text may mix document text with other structures, use incorrect run boundaries,
represent a different internal ordering, or require additional framing that is
not yet understood. The evidence does not distinguish among those hypotheses.

Do not add `.inp` to the format registry, route native InPage into production,
claim layout support, or close issue #38. Any follow-up must begin from a new,
independently stated structural hypothesis and add regression tests before
changing extraction behavior.
