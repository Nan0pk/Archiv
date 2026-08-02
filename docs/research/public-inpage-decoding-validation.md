# Public native InPage decoding validation

Status: research only. Native `.inp` remains outside Archiv's supported-format registry.

## Verified repository baseline

This work starts from Archiv `main` commit
`8b851cd8e785c1b755e718bbbcff562196fa5f0d`. Issue #38 remains open. Closed,
unmerged PR #49 established pinned structural measurements for two public native
candidates but did not decode or publish source text.

## Inputs under investigation

The first public candidates are pinned to
`ShakesVision/html-experiments@1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a`.
The source repository publishes an attribution licence, but it does not establish
the original creator, commercial InPage build, redistribution chain, or independent
text/layout ground truth for each binary. The binaries are therefore transient,
non-redistributed research inputs, not production fixtures.

Known PR #49 identities:

| Candidate | Git blob SHA-1 | File SHA-256 | Expected stream |
|---|---|---|---|
| `juz_29.inp` | `b5c5774f41ea84a4b7ad6c859f0576da70604925` | `81c61955c2eb38fb14c100fdb36c642ee8e0f6d005109e894c24249617939ffa` | `InPage300` |
| `khali hathely (1).inp` | `c7f69058ca0024be6866531429292967ad852ef1` | `a3c6a60de0057345849529213d7a216a4f7d9b278434db883597e311ec1ab276` | `InPage100` |

Every download must match its pinned Git blob identity and SHA-256 before parsing.
Downloaded bytes must remain temporary, must not be uploaded as an artifact, and
must be deleted after measurement.

## Independent implementation

The research-only implementation is split into:

- `src/archiv/research/inpage_types.py` for bounded shared types and sanitized metrics;
- `src/archiv/research/inpage_container.py` for validated root-stream selection and
  conservative InPage300 extraction;
- `src/archiv/research/inpage_legacy.py` for InPage100 framing, mapping and conflict
  measurement;
- `src/archiv/research/inpage_validation.py` for pinned Git identities, private
  mode-0600 output and explicit Quran comparison modes.

The modules are disconnected from production ingestion. Together they:

- reuse Archiv's bounded CFB validation primitives;
- require root-level `DocumentInfo` and exactly one root-level `InPageNNN` stream;
- reject mini-stream content, ambiguous candidates, malformed chains and oversize
  streams;
- hash the selected stream;
- extract private text only in memory unless an explicit exclusive mode-0600 path is
  supplied;
- serialize sanitized hashes and counts, never document text;
- always record `native_support_claimed: false`.

## Evidence classes

- **Verified fact:** the pinned source paths and Git identities observed by PR #49.
- **Direct measurement:** hashes, counts and comparison metrics produced by an exact
  workflow run.
- **Source claim:** parser behavior or format interpretation stated by another
  repository.
- **Hypothesis:** a record, stream suffix or normalization interpretation not yet
  independently validated.
- **Limitation:** incomplete provenance, missing creator version or missing layout
  truth.

## Current decision

The work justifies bounded experimental decoding, not production ingestion. A narrow
InPage300 prototype becomes eligible only after at least two files match independent
lawful Unicode ground truth with logical order and normalization differences reported.
InPage100 additionally requires reconciliation of independently authored mappings,
record framing, punctuation and numeral behavior. No page, story, frame or object
locator is claimed.
