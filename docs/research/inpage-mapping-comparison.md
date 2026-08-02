# Native InPage mapping comparison

## Sources and legal boundary

The evidence run compared two independently authored public sources fetched at pinned
commits and deleted before artifact upload:

1. `ShakesVision/html-experiments@1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a`
   `InpageToUni.xml`, SHA-256
   `99e6c6df3978b1be9fca803a512f23a9cb544eb56e66966144bc3972d7331b5b`;
2. `KamalAbdali/InpageToUnicode@6eab0278d3717de98c230712121e4460266755b8`
   `InpToUni.c`, SHA-256
   `00bd847a4e39b96e283b5aafe418a6ec8db76eaf429aa38da7d637d59b6ba4ce`.

The source files are not copied into Archiv. Kamal Abdali's repository does not expose
a clear licence in the inspected tree, so its source is research evidence only and may
not be incorporated or redistributed.

## Direct measurements

The XML parser retained the first mapping for each byte code and measured:

- 130 retained codes;
- 47 duplicate keys;
- all 47 duplicate keys conflicted with the retained first value;
- three ignored rows.

The C table produced 227 default byte values after excluding 29 explicit special cases.
The comparison measured:

- 106 overlapping byte codes;
- 71 agreements;
- 35 conflicts;
- 24 XML-only codes;
- 121 C-only codes.

Conflicting byte codes are:

`34, 169, 175, 177, 178, 183, 186, 187, 188, 192, 193, 194, 195, 196, 197, 198, 201, 204, 205, 206, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 229, 240, 244, 249, 255`.

Agreement is supporting evidence, not textual ground truth. Conflict does not prove
which source is wrong; version differences, special-case handling or inherited errors
remain possible.

## Decision

No mapping source is silently preferred for production. The research decoder records
its chosen source hash, output hash and unmapped escape count. Native InPage100 support
requires lawful, independently validated mapping semantics with explicit version
coverage and reconciliation or bounded exclusion of every measured conflict.
