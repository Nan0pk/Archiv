# Native InPage converter assessment

## Decision rule

A converter is relevant only if it reads native binary `.inp` input. Text pasted from InPage, PDF/TXT exports and OCR do not qualify.

## Candidate assessment

| Candidate | Native binary evidence | Layout evidence | License/source evidence | Privacy and security | Current decision |
|---|---|---|---|---|---|
| CLE InPage-to-Unicode converter | CLE describes converting InPage files/text to Unicode text files, but the binary contract has not been independently inspected | No proven page/story/frame output | Public page and user guide exist; source, exact license and binary identity remain unverified | Must be inspected statically and run only in a disposable no-network sandbox | Highest-priority adapter candidate, not accepted |
| Legacy InPage application | Opens native files | Potential visual truth source | Proprietary licensed application | Known crafted-document code-execution history; unacceptable as an untrusted parser | Use only to author controlled fixtures and exports in an isolated licensed environment |
| Open-source glyph/text mappers | No native binary parsing established | None | Source and licenses vary | Useful offline as mapping evidence | Research aid only |
| Online conversion services | Some claim native conversion | Output semantics unknown | Service terms, implementation and versions unknown | Uploading source documents violates Archiv’s privacy boundary | Rejected |
| Direct bounded parser | No implementation yet | Potentially best when records are understood | Archiv-controlled | Best long-term isolation and determinism | Preferred after lawful fixtures and comparative evidence |
| Version-specific hybrid | No implementation yet | Could combine direct parsing and an optional adapter | Complex | Larger attack and maintenance surface | Premature |

## CLE inspection checklist

Before any execution:

- obtain the converter from an authoritative CLE location;
- record SHA-256, byte size, signer and timestamps;
- capture displayed license text and redistribution terms;
- identify executable format, imports, bundled runtimes and packers;
- inspect for network endpoints, telemetry, update behavior and temporary paths;
- determine whether it has CLI, batch, COM or GUI-only operation;
- determine accepted extensions and version errors;
- determine exact output encoding and filename behavior.

Controlled execution must use:

- a disposable Windows VM or similarly isolated environment;
- network denied;
- no shared clipboard;
- a temporary profile;
- one read-only controlled fixture;
- one controlled output directory;
- CPU, memory and wall-clock limits;
- process-tree termination;
- filesystem and process monitoring;
- hashes for input, executable and every output;
- sanitized logs that exclude private text.

Required measurements:

- deterministic repeated output;
- supported/unsupported version behavior;
- corrupt/truncated behavior;
- unrelated CFB and unrelated `.inp` behavior;
- protected/encrypted behavior;
- Unicode normalization;
- Urdu/Arabic/Perso-Arabic accuracy;
- punctuation and numeral behavior;
- whether page, paragraph, story or frame boundaries survive.

## Adapter acceptance threshold

A local adapter may enter Archiv only when:

1. its identity and license are pinned;
2. all processing remains local and offline;
3. hostile unrelated files fail closed;
4. output is deterministic;
5. output matches lawful fixture ground truth;
6. errors distinguish unavailable, unsupported, protected, malformed, timeout and failure;
7. no private text appears in public CI artifacts;
8. the adapter remains optional;
9. canonical original bytes remain untouched;
10. full ingestion and downstream lifecycle tests pass.

## Current conclusion

Do not integrate a converter yet. First merge the evidence package and bounded metadata probe, then acquire lawful fixtures and independently inspect the CLE candidate.
