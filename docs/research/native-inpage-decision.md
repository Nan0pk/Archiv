# Native InPage engineering decision

## Current decision

Keep `.inp` explicitly unsupported. Merge research evidence, fixture infrastructure and bounded CFB inspection only.

## Options

| Approach | Evidence strength | Text accuracy potential | Layout potential | Security/privacy | Portability | Moving parts | Decision |
|---|---|---|---|---|---|---|---|
| Explicit rejection | Strong and honest | N/A | N/A | Best | Best | None | Current production behavior |
| Direct bounded parser | Insufficient today | High if records are proven | Highest long-term potential | Best controllable boundary | Linux/Windows | Low to moderate | Preferred future path |
| Sandboxed local converter | Candidate evidence only | Unknown until measured | Probably limited | Higher risk | Likely Windows-specific | Moderate to high | Fallback after inspection |
| Version-specific hybrid | Premature | Version-dependent | Version-dependent | Complex | Complex | Highest | Defer |
| Online converter | Service claims only | Unknown | Unknown | Violates no-upload rule | N/A | External service | Reject |
| OCR/export ingestion | Does not meet definition | Loses native evidence | Visual-only or export-only | Misleading | Easy | Low | Reject as native support |

## Phased route

### Evidence infrastructure

- bounded CFB probe;
- machine-readable evidence register;
- fixture schema;
- lawful acquisition kit;
- explicit confidence language.

### Fixture acquisition

- obtain known-version owned native files;
- verify redistribution permission;
- record Unicode and layout ground truth;
- create controlled comparison pairs and negatives.

### Format experiments

- inspect root-level streams and stream hashes;
- compare one-property changes;
- identify version markers and text-bearing records;
- test language and mixed-direction mappings;
- establish whether page/story/frame relations are recoverable.

### Prototype choice

Choose a direct parser when enough fields can be explained and bounded. Choose an optional local adapter only when direct parsing is technically or legally infeasible and the adapter passes the full acceptance gate.

### Production integration

Only then:

- register exact supported native variants;
- perform content/version validation before canonical storage;
- preserve original bytes;
- produce deterministic normalized text;
- expose only proven locators;
- add explicit unsupported/protected/malformed statuses;
- run complete lifecycle, privacy and offline verification.

## Fail-fast decision points

Stop a parser experiment when:

- a field interpretation does not reproduce across controlled fixtures;
- decoding requires guessed offsets or silent recovery;
- text order cannot be independently verified;
- language mapping destroys distinctions;
- a version cannot be identified by content;
- malformed input causes unbounded work.

Stop an adapter experiment when:

- license or redistribution is unclear;
- network access cannot be disabled;
- output is nondeterministic;
- unsupported input produces partial success;
- the tool leaks filenames or text;
- hostile files escape resource or filesystem boundaries.

## Success threshold for the first narrow capability

The first claim may be limited to one explicitly identified legacy generation and text-only extraction. It still requires:

- at least three lawful positive fixtures;
- unrelated CFB and unrelated `.inp` rejection;
- corrupt and unsupported-version failures;
- Urdu-only and mixed Urdu/English ground truth;
- deterministic rebuild;
- no stream execution;
- no network;
- complete audit metadata;
- full Archiv lifecycle tests.

No page/frame locator claim is permitted until layout structure is separately proven.
