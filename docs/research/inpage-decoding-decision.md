# Native InPage decoding decision

## Decision

Merge only the bounded research infrastructure. Do not register `.inp`, connect the
decoder to ingestion, claim native support, claim layout recovery, or close issue #38.

## Directly measured progress

Evidence run `30771593543` on Archiv head
`081a0f8f7bd7ba87dc219f34ac4f19f371113fa3` verified pinned Git identities and
measured five public native candidates without retaining document bytes or text.

- Two `InPage300` streams extracted deterministically: 54,688 and 68,020 code points,
  with 44,910 and 54,508 Arabic/Perso-Arabic code points.
- Three `InPage100` streams extracted deterministically under the labelled u32
  assumption.
- The u16 and u32 record interpretations diverged on every InPage100 file. Accepted
  u16 candidates with non-zero upper header words numbered 45, 151 and 72.
- The two independently authored mapping sources overlapped on 106 byte codes, agreed
  on 71 and conflicted on 35. The XML source itself contained 47 duplicate conflicting
  keys under first-key-wins parsing.

These measurements establish technical feasibility and expose decisive ambiguity.
They do not establish exact text, logical reading order, creator version/build,
protection status, layout structure or lawful redistribution.

## Remaining gate

### InPage300

- compare both private extracted texts with a pinned lawful Quran corpus;
- report exact NFC, whitespace, diacritic-insensitive and verse-symbol modes
  separately;
- verify verse order, numerals, punctuation and direction through human review;
- obtain a bounded family/version identification rule.

### InPage100

- determine the authoritative record-header meaning rather than choosing whichever
  yields more readable output;
- reconcile or explicitly scope the 35 overlapping mapping conflicts and remaining
  unmapped escape codes;
- validate punctuation, numerals, marker stripping and binary/style filtering against
  independent text truth;
- obtain lawful known-version fixtures.

### Both families

Obtain lawful redistributable fixtures with creator build, Unicode ground truth and
page/story/frame truth. Until then, explicit rejection is the correct production
behavior.
