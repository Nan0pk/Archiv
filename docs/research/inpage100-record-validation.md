# InPage100 record validation

## Direct structural evidence

PR #49 measured `khali hathely (1).inp` as CFB v3 with a 378,229-byte `InPage100`
stream whose SHA-256 is
`a3bb14053cb2cb155a4caa282b6e49af196379d96b084d1dec39646b7d312e7c`.
The aggregate scan found 21,117 candidate `0x04` escapes and 2,253 plausible
length-prefixed records containing 110,024 bytes; 20,290 candidate escapes occurred
inside those records.

## Framing discrepancy that must be measured

Two independent public implementations disagree in wording:

- the newer JavaScript implementation treats the four header bytes as a little-endian
  `uint32` length;
- Kamal Abdali's older C implementation consumes four header bytes but derives record
  length from only the first two bytes.

The research extractor therefore measures both interpretations. It records candidate
offsets shared by both, offsets unique to either interpretation, and the number of
non-zero upper 16-bit header words. Extraction currently follows the four-byte
interpretation only as a labelled research assumption because it reproduces PR #49's
aggregate measurement. Real fixtures must prove whether the upper word is always zero.

## Mapping evidence

The extractor can compare, without bundling either source:

- an `InpageToUni.xml`-style table, preserving first-key-wins behavior while counting
  duplicate and conflicting entries;
- the derived `unicodebyte[256]` interpretation from a transient copy of
  `KamalAbdali/InpageToUnicode/src/InpToUni.c`.

Special punctuation and honorific cases are explicit. Every overlap, agreement,
conflict and source-only code is counted. No source is silently preferred when they
disagree.

## False-positive and safety testing

Required negatives include random bytes, unrelated CFB streams, picture streams,
truncation, mutated lengths, non-zero upper length words, excessive records, cycles,
ambiguous `InPageNNN` streams and mini-stream content. Readable Urdu alone is not proof
of correctness.

## Readiness threshold

InPage100 remains not ready until record framing reproduces across multiple files,
mapping conflicts are explained, decoded text is independently validated, punctuation
and numeral order are verified, and binary/style records are excluded without guessed
recovery.
