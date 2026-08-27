# Definition of done

A capability is done only when:

- its purpose and contract are documented;
- input and output schemas exist;
- allowed and forbidden effects are explicit;
- synthetic fixtures exist;
- unit tests exist;
- an independent validator exists;
- failure states are tested;
- source integrity is tested;
- offline behaviour is tested where claimed;
- CI preserves sufficient evidence;
- user documentation is updated;
- generated artifacts can be inspected outside Archiv.

Installation, a green process exit, or a model statement is not task success.

## Beta readiness: private local-corpus trial v1

Beta is **GO** only after `ARCHIV-PRIVATE-TRIAL-1.0.0` completes every mandatory core cell on the
Minimum, Recommended, and Workstation Fedora tiers, the bounded model matrix has no unexplained
missing cell (capacity-blocked cells are allowed only where the protocol permits), and every
eligible collection/model cell meets all thresholds below. Aggregating a failing cell into a pass
is forbidden.

| Gate | Go threshold |
|---|---:|
| Supported-file ingestion success | >=99.5%, with 100% for clean required-source documents |
| Unsupported-file classification | 100% correctly rejected and reasoned; zero counted as processing failures |
| Degraded-file detection | >=95% of gold degradations detected; zero silent total-content losses |
| Required-source recall at evidence limit 8 | >=95% per cell and >=99% for current-revision questions |
| Citation correctness | >=99%; zero fabricated IDs, stale revisions, or hash-invalid citations |
| Unsupported factual claims | <=1% and zero high-impact false claims presented as verified |
| Answer usefulness | >=90% scoring >=4/5; negative-question honest refusal >=95% |
| Report usefulness | >=90% scoring >=4/5 and 100% structural validation |
| Retrieval latency | warm p95 <=2 s Small, <=5 s Medium, <=10 s Large |
| Model `ask` latency | warm p95 <=120 s; timeout rate <=1% |
| Deterministic report latency | p95 <=60 s Small, <=180 s Medium, <=300 s Large |
| Resource ceiling | peak Archiv process-tree RSS <=75% physical RAM; no OOM |
| Disk growth | final non-model `ARCHIV_HOME` <=2.5x source bytes and peak <=3.0x |
| Interrupted-run recovery | 100% of 9 core scenarios; no corruption/data loss; <=2x clean indexing time |
| Privacy/export | zero forbidden fields or network egress; two-person review complete |

Any source mutation, database corruption, private-data export, non-loopback model traffic, silent
complete extraction loss, fabricated citation, or high-impact unsupported claim is an automatic
**NO-GO**, regardless of averages. A model band that cannot meet its gates is documented as
unsupported for beta rather than hidden. The current execution status is recorded in
[`trial-results-v1.md`](trial-results-v1.md).
