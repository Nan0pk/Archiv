# Native InPage mapping comparison

## Sources

The first comparison targets two independently authored public sources, fetched only
in an ephemeral research job:

1. `ShakesVision/html-experiments` mapping XML pinned to commit
   `1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a`;
2. `KamalAbdali/InpageToUnicode/src/InpToUni.c` pinned to commit
   `6eab0278d3717de98c230712121e4460266755b8`.

The source code and mapping files are not copied into Archiv. The evidence register
retains only source identities, hashes and derived aggregate comparison metrics.
Kamal Abdali's repository does not expose a clear licence file in the inspected tree,
so its source is research evidence only and must not be redistributed or incorporated.

## Comparison rules

- parse files with hard size and entry bounds;
- reject malformed or out-of-range entries;
- for duplicate XML keys, retain the first mapping and count later duplicates and
  conflicts;
- apply explicit punctuation/honorific cases separately;
- compare all overlapping byte codes;
- publish conflicting byte codes, not mapped source text;
- preserve one output hash per interpretation when decoding a private fixture.

Agreement between the two sources is supporting evidence, not independent textual
ground truth. Both may share earlier conventions or errors.
