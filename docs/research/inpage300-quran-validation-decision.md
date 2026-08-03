# InPage300 Quran validation decision

## Decision

The current bounded aligned UTF-16LE InPage300 extractor failed independent Quran
text validation. It is not a defensible production parser.

Native `.inp` remains outside the supported-format registry, no production
routing is permitted, no layout support is claimed and issue #38 remains open.

## Evidence classification

- **Verified fact:** the two native candidates and both Unicode references were
  pinned by commit, blob or SHA-256 identity.
- **Direct measurement:** extraction repeated with stable private hashes, but no
  success-eligible normalization produced complete in-order verse coverage.
- **Reproducible inference:** the automated sequence gate is not satisfied for
  any of the four file/reference pairs.
- **Limitation:** bounded whole-text operation counts for these large texts are
  position-aligned and nonminimal, not a minimal edit script.
- **External blocker:** no qualified Urdu/Arabic human review occurred.
- **Hypothesis:** the accepted UTF-16 runs may contain mixed structures,
  incorrect boundaries or non-logical internal ordering. The evidence does not
  yet choose among these explanations.

## Exact failed gate

Juz 29 expected 431 verses and Juz 30 expected 564 verses. Primary-mode in-order
matches were:

- Juz 29 versus Tanzil: 5 exact NFC, 6 whitespace-normalized, 14
  diacritic-insensitive and 6 verse-symbol-normalized;
- Juz 29 versus amrayn: 3, 3, 5 and 3;
- Juz 30 versus Tanzil: 2, 2, 33 and 2;
- Juz 30 versus amrayn: 0, 0, 7 and 0.

Every primary mode had zero contiguous opening verses and failed on the first
expected verse. Arabic-letters-only stripping remained diagnostic and could not
satisfy the gate.

The exact hashes, lengths, matches, bounded operation counts, punctuation and
numeral differences, first and last matched verse identifiers and ordering flags
are in `inpage300-quran-ground-truth-measurements.json`.

## Failed hypotheses

The results reject these propositions for the two measured files:

1. every aligned allowed UTF-16LE run in the root `InPage300` stream is logical
   document text;
2. concatenating those accepted runs yields complete Quran reading order;
3. whitespace, diacritic or verse-symbol normalization alone explains the
   differences;
4. Arabic-range stripping provides valid correctness evidence;
5. a high Arabic character count is evidence of correct extraction.

## Human review protocol

A qualified reader must eventually inspect private local output for the opening
and ending verses, order, omissions, duplicates, punctuation, numerals,
diacritics, joining and bidi controls, unexpected metadata and logical reading
order. No such review is claimed here. Since automated opening coverage already
fails, human review is diagnostic rather than an authorization to ship.

## Next defensible work

Do not tune thresholds or add broader stripping to improve similarity. A new
research slice should first identify a structural discriminator for text runs,
record boundaries or ordering, then add synthetic and real-file regression tests
before rerunning the same pinned references.

Bounded InPage100 work remains separate. No production InPage100 prototype is
permitted until its u16/u32 record framing and mapping conflicts are independently
resolved across multiple files.

## Repository boundary

This result is durable research evidence only. It deliberately does not commit
native fixture bytes, extracted document text, Quran reference text or third-party
mapping code. It does not add a downloader, register `.inp`, change canonical
storage, infer an InPage product version, or close issue #38.
