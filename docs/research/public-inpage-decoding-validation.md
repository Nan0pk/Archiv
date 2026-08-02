# Public native InPage decoding validation

Status: research only. Native `.inp` remains outside Archiv's supported-format registry.

## Verified baseline and evidence run

The branch starts from Archiv `main` commit
`8b851cd8e785c1b755e718bbbcff562196fa5f0d`. Issue #38 remains open.

Evidence run `30771593543` executed on research head
`081a0f8f7bd7ba87dc219f34ac4f19f371113fa3`. It checked out the two external
repositories at exact commits, verified every Git blob identity, extracted each file
twice, deleted the external trees and uploaded one ASCII-only JSON artifact:

- artifact ID: `8840691094`;
- ZIP SHA-256:
  `e9b60f4a48cea7ea149ffd67bfae428b9f804cbc037dc033ab1f352ccdbbf2a1`;
- no fixture bytes, mapping source or decoded document text retained.

The public files remain provenance-limited research candidates. Repository-level
attribution terms do not establish the original creator, commercial InPage build,
literary rights, redistribution chain or independent text/layout truth for each file.

## Independent implementation

The research-only implementation is split into:

- `inpage_types.py` for bounded shared types and sanitized metrics;
- `inpage_container.py` for validated root-stream selection and conservative
  InPage300 extraction;
- `inpage_legacy.py` for InPage100 framing, mapping and conflict measurement;
- `inpage_validation.py` for pinned Git identities, exclusive mode-0600 private output
  and explicit Quran comparison modes;
- `scripts/run_public_inpage_evidence.py` for reproducible evidence from already pinned
  local checkouts.

The modules are disconnected from ingestion, require root-level `DocumentInfo` and
exactly one root-level `InPageNNN` stream, reject mini-stream/ambiguous/malformed and
oversize content, emit sanitized hashes/counts and always record
`native_support_claimed: false`.

## Measured candidates

All five pinned candidates measured deterministically:

| Candidate | Stream | Stream bytes | Text code points | Arabic/Perso-Arabic | Lines |
|---|---|---:|---:|---:|---:|
| `juz_29.inp` | `InPage300` | 410,578 | 54,688 | 44,910 | 4 |
| `juz_30.inp` | `InPage300` | 543,086 | 68,020 | 54,508 | 55 |
| `khali hathely (1).inp` | `InPage100` | 378,229 | 24,912 | 15,767 | 1,113 |
| `Urdu Grammer Book.inp` | `InPage100` | 938,344 | 64,241 | 42,582 | 2,217 |
| `Zakiya Mashhadi.inp` | `InPage100` | 556,142 | 174,429 | 135,996 | 516 |

These counts are measurements under explicit research algorithms, not proof that every
retained character is correct document text.

## Material findings

### InPage300

Two files contain large, stable aligned Unicode/Perso-Arabic regions with deterministic
raw and NFC hashes. This advances technical feasibility but does not verify Quran text,
logical order, punctuation, numerals or omitted/extra regions. Run boundaries are not
layout locators.

### InPage100

The u16 and u32 record interpretations diverge in all three files. Accepted u16
candidate headers with non-zero upper 16-bit words numbered 45, 151 and 72. This proves
that the old-C/new-JavaScript difference cannot be dismissed as equivalent framing.

The two mapping sources overlapped on 106 byte codes, agreed on 71 and conflicted on
35. The XML source also had 47 conflicting duplicate keys. Under the XML interpretation,
the files retained 125, 133 and 148 unmapped escape pairs.

## Decision

The work justifies merging bounded research infrastructure, not production ingestion.

- InPage300 still requires independent lawful Quran ground-truth comparison and human
  reading-order review.
- InPage100 still requires an authoritative framing rule and reconciled or explicitly
  version-bounded mappings.
- Both require lawful redistributable known-version fixtures with human-reviewed text
  and layout truth.

No page, story, frame, object, version or layout support is claimed. Explicit `.inp`
rejection remains the truthful product behavior.
