# Native InPage format map

This document separates evidence from hypotheses. It is not a native-format specification.

## Container layer

| Property | Current status | Engineering consequence |
|---|---|---|
| Generic container | Microsoft Compound File Binary Format is verified as a general-purpose stream container | CFB magic is necessary for some legacy candidates but never sufficient |
| Signature | `D0 CF 11 E0 A1 B1 1A E1` is the CFB signature | Validate header and structure before directory inspection |
| CFB versions | Major versions 3 and 4 use 512-byte and 4096-byte sectors respectively | Bound and validate sector sizes, FAT, DIFAT and directory chains |
| Application identity | Unknown from the generic header | Require reachable application-specific structures |
| `InPage100` | Supported by public vulnerability analysis | Treat as a research indicator only |
| `DocumentInfo` | Community/file-identification lead | Conservative companion indicator, not an authoritative signature |
| `InPage200` / `InPage300` | Unverified discovery leads | Report names without product-version mapping |
| Split `.bNN` files | Unverified discovery lead | Classify separately; do not ingest |

## Text and encoding layer

| Question | Status |
|---|---|
| Is legacy InPage text Unicode internally? | CLE states that InPage uses a different encoding scheme |
| Is clipboard/glyph mapping available? | Yes, but text mappings do not parse native document structure |
| Are on-disk text records documented? | No complete authoritative public description found |
| Are language-specific variations documented? | Not sufficiently for production parsing |
| Can logical reading order be reconstructed? | Unknown |
| Can text be separated from formatting records? | Unknown |
| Are compression or obfuscation used? | Unknown |
| Are protected/encrypted variants identifiable? | Unknown |

## Layout layer

No independently verified public mapping currently exists for:

- page records;
- stories;
- linked or unlinked text frames;
- paragraph records;
- tables;
- images and non-text objects;
- frame creation order versus visual reading order;
- page, story and object locators.

Archiv must not emit these locators until controlled fixtures prove them.

## Candidate classification policy

The research probe uses a deliberately conservative policy:

1. Enforce a file-size limit before reading.
2. Validate the CFB header, version, byte order and sector sizes.
3. Bound FAT, DIFAT, directory sectors, entries and tree depth.
4. Follow only the directory tree reachable from the root.
5. Ignore orphan directory entries for classification.
6. Consider only root-level streams.
7. Report a candidate when `DocumentInfo` coexists with a root-level `InPageNNN` name.
8. Never map `NNN` to an InPage product version.
9. Never read stream contents.
10. Never return a supported-ingestion status.

## Negative classes

The fixture corpus must include:

- plain-text `.inp`;
- Abaqus-style `.inp` beginning with `*Heading`;
- unrelated CFB documents;
- CFB with orphan InPage-like names;
- CFB with only `DocumentInfo`;
- CFB with only an `InPageNNN` name;
- malformed or cyclic FAT/DIFAT/directory chains;
- unsupported CFB major versions;
- oversized files;
- truncated headers and sectors.

## Confidence language

Use these terms consistently:

- **Verified:** supported by an authoritative specification or independently reproducible evidence.
- **Strongly supported:** supported by detailed technical analysis but not a public format specification.
- **Discovery lead:** useful for fixture acquisition or controlled experiments only.
- **Inference:** a reasoned conclusion that still requires measurement.
- **Unknown:** no adequate evidence.
