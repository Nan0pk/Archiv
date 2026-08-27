# Private trial v1 execution register

**Protocol:** `ARCHIV-PRIVATE-TRIAL-1.0.0`  
**Register updated:** 2026-08-27  
**Verdict:** **NO-GO (insufficient evidence)**

No consented private corpus, three eligible Fedora hardware tiers, or complete bounded local-model
matrix was available in this repository execution environment. Those cells were not fabricated.

The repository's deterministic 12-document/22-question public fixture was rerun as an operational
smoke check. It is synthetic, does not satisfy any private corpus size stratum, and is not a v1
matrix cell. The older 2,097-file Fedora run is also excluded because it predates the frozen
protocol, used one host and one model configuration, and lacks several required measures.

| Evidence | Scope | Sanitized outcome | v1 eligibility |
|---|---|---|---|
| Public fixture rerun | synthetic loopback fixture | completed; exact aggregate retained in local command output | smoke only |
| 2026-08-24 field notes | one Fedora host; 2,097 private files; Ollama micro model | dominant measured failures: watermark PDF extraction, unsupported model claims, interrupted-ingest recovery, report validation, and retrieval crowding | historical input only |
| Minimum tier matrix | Small/Medium core; bounded models | not run: eligible host/corpus unavailable | missing |
| Recommended tier matrix | Small/Medium/Large core; bounded models | not run: eligible host/corpus unavailable | missing |
| Workstation tier matrix | Small/Medium/Large core; bounded models | not run: eligible host/corpus unavailable | missing |

Only non-sensitive failure categories and previously published aggregate counts were used to
create the lawful synthetic regression-fixture catalog. No private source, prompt, answer,
filename, path, or model output was copied. Beta remains no-go until every mandatory cell has a
reviewed sanitized aggregate and all thresholds pass.

