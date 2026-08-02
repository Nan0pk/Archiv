# InPage100 record validation

## Measured candidates

Evidence run `30771593543` measured three pinned `InPage100` streams
reproducibly without publishing text:

| Candidate | u32 records | u16 records | Shared | u32 only | u16 only | Accepted non-zero upper words |
|---|---:|---:|---:|---:|---:|---:|
| `khali hathely (1).inp` | 2,253 | 2,275 | 2,230 | 23 | 45 | 45 |
| `Urdu Grammer Book.inp` | 1,762 | 1,876 | 1,718 | 44 | 158 | 151 |
| `Zakiya Mashhadi.inp` | 645 | 696 | 611 | 34 | 85 | 72 |

The first file also reproduced PR #49's structural measurements exactly: 21,117
candidate `0x04` escapes, 2,253 u32 records, 110,024 record bytes and 20,290 valid
escape pairs inside those records.

## Framing finding

The framing discrepancy is real, not theoretical:

- the newer JavaScript implementation interprets the four header bytes as a
  little-endian `uint32` length;
- Kamal Abdali's older C implementation consumes four header bytes but derives record
  length from only the first two bytes;
- accepted records with non-zero upper 16-bit words occur in all three real files.

Extraction currently follows the u32 interpretation only as a labelled research
assumption because it reproduces PR #49. The measurements do not prove that assumption
is authoritative. No production parser may silently choose u16, u32 or a hybrid based
on output readability.

## Mapping finding

The pinned XML mapping produced 130 first-key values, 47 duplicate keys and 47
conflicting later values. The independently authored C table produced 227 default
values after excluding explicit special cases. Across 106 overlapping codes, the
sources agreed on 71 and conflicted on 35. The public files still contained 125, 133
and 148 unmapped escape pairs under the XML interpretation.

Kamal Abdali's repository has no clear inspected licence, so its table remains
research evidence and is not incorporated into Archiv.

## Readiness threshold

InPage100 is not ready. It requires an authoritative framing rule, reconciled or
explicitly scoped mapping conflicts, independently verified text, correct punctuation
and numeral handling, bounded binary/style filtering, malformed-input rejection and
lawful known-version fixtures. Readable Urdu is insufficient evidence.
