# Exact Solution: From File Archive to Institutional Memory That Finds Smartly — Offline and Online

**Date:** 2026-10-25 | **Branch:** `arena/476a6970-archiv` | **Read after:** `INSTITUTIONAL_MEMORY_ANALYSIS.md`

---

## 0. What You Are Asking — In My Words

You built Archiv to preserve files with proof (hash, citation, validator). That part works.

What you want now is:

1. **Smart finding at scale** — not "words match" but "meaning matches", so a question in different words still finds the right paragraph among 100k files.
2. **Institutional memory** — not just search, but: who wrote it, is it still current or replaced, who can see it, and proof for every answer.
3. **Dual mode from one codebase** — same Archiv works if the vault never leaves the laptop (offline model) *and* if the user wants to use OpenAI/Claude/any frontier model (online). The user chooses per vault, explicitly, and every answer is stamped with which path it took.

Is it doing 1 today? **No.** Is it the smartest way for large data? **No.** Below is exactly why and exactly what to build.

---

## 1. Why It Is Not Smart Today — Proof

**Current pipeline** (`src/archiv/search/retrieval.py` + `search/index.py`):

```
Your question → split into exact phrases → search SQLite FTS5 for quoted phrases → score by weight + position → 1 best passage per source → cap at 8
```

Specific rules in code right now:

| Piece | Exact Value | File |
|---|---|---|
| Full question weight | 120 | `derive_query_variants(): exact-objective` |
| Hand-written concept expansions | 9 concepts, e.g. `unfinished → "unfinished work, remains unresolved, not yet"` | `_CONCEPTS` dict |
| Derived phrases | max 6 phrases of 3 or 2 words, weight 80 | loop over `phrase_tokens` |
| Single keywords | max 8 terms, weight 60 | `derived_terms` |
| Ranking | `score = weight + position_bonus + information_bonus` — no meaning | `retrieve_evidence()` |
| Index rebuilt | Full rebuild re-hashes *every* original every `archiv add` | `rebuild_search_index()` |

**Concrete failure:**
Doc: `Q3 revenue fell 12% on supply disruption`
Question: `how did sales perform last quarter?`
Result: 0 hits. Zero words overlap. System returns nothing or wrong generic hits. A smart system would rank it top.

**Measured on real data:** Your own field notes (2026-08-23, 2,098 files): `archiv find` gets 10/20 real questions right. Two large digest files dominate rankings because frequent words win. This is not a bug to patch with more concepts — it is the ceiling of literal search.

---

## 2. What "Smart" Means for Large Institutional Data

An institution needs 5 things beyond literal search:

1. **Meaning match** — paraphrase, synonym, different language for same fact
2. **Precision when needed** — contract `#2024-1147` must match exactly, not approximately
3. **Re-ranking by answer value** — the passage that actually answers the question outranks a passage that just contains the words
4. **Time and authority awareness** — new policy outranks old, approved outranks draft
5. **Scale without full rebuild** — adding 10 files to 100k should not re-hash 100k

No single technique does all five. Smart is **hybrid**.

---

## 3. Exact Architecture That Solves All Three Asks

### The one diagram to keep

```
                        PRESERVATION CORE (never changes)
                        originals/sha256/… (immutable, 444) + archiv.sqlite3 (objects/ingestions)
                        derived/normalized/document.json (segments with locators)
                                     ↑
                                     | citation validates here every time
                                     |
        SMART FINDING LAYER (rebuildable, deletable)
        ┌──────────────────┬───────────────────┐
        │ FTS5 index       │ Vector index       │  ← both built from same segments, both rebuildable
        │ exact words      │ meaning (vectors)  │     indexes/ folder, excluded from backup just like FTS today
        └────────┴──────────────────┘
                        ↓ merge (RRF) → rerank → validate → evidence_limit
                                     |
        ANSWER LAYER (per-vault choice)
        ┌──────────────────┬───────────────────┐
        │ OFFLINE VAULT    │ ONLINE VAULT       │
        │ local embed      │ remote embed       │  ← same interface, different provider
        │ local rerank     │ remote rerank      │
        │ local LLM        │ frontier LLM       │
        │ 127.0.0.1 only   │ https + consent    │
        │ stamped: local   │ stamped: external  │
        └──────────────────┴───────────────────┘
                                     |
                        INSTITUTIONAL LAYER (thin, on top)
                        superseded-by links, valid-from/until dates, per-vault access note
```

**What never changes:** Preservation core, derived structure, `models propose / validators decide`, citation revalidation before model and after model, `ARCHIV_HOME` layout versioning, schema migrations, egress-denied acceptance, no runtime downloads, lawful fixtures only.

### 3.1 Smart Finding — Exact Spec

**A. Chunking** — current `NormalizedDocument.segments` stay as is (they carry the legal locator: page/cell/slide). Add a second table for search only:

`indexes/semantic.sqlite3` (or `indexes/vectors.db` — name does not matter, but it is rebuildable):

- Table `semantic_chunks`: `chunk_id, object_sha256, segment_indexes (JSON), text, text_sha256, locator_json` — each chunk is ~400-500 tokens with 80-token overlap, built from consecutive segments, never crossing document boundary. Preserves locator of first segment for citation.
- Table `embeddings`: `chunk_id, vector (blob, float32), model_id, model_sha256`

Chunking is deterministic, tested, versioned. Bumping chunk version enqueues re-embed, same pattern as your existing `processing_queue`.

**B. Embedding model** — pinned file, not name:

- Ship under `models/embeddings/bge-small-en-v1.5-onnx/` or `e5-small` — Apache/MIT, ~120MB, multilingual if you need Urdu/Arabic, quantized ONNX for CPU. Record `path + sha256` in `config/embeddings.json` just like `PinnedTokenizer` in `cost_control.py`. No `pip install sentence-transformers` that downloads at runtime — that breaks egress-denied acceptance.
- Offline vault: `LocalEmbedProvider` loads ONNX via `onnxruntime` (already optional dep) — pure CPU, no network.
- Online vault: `RemoteEmbedProvider` calls `POST https://api.openai.com/v1/embeddings` (or any OpenAI-compatible) — requires same `config/evaluation.json` mark + spend ceiling as LLM. Reuses your `cost_control.decide_spend()` — count embedding tokens before call, refuse if over ceiling.

**C. Query flow** — replaces `derive_query_variants()` only:

```
query = "how did sales perform last quarter?"
  ├─ Lexical branch (existing, keep it): derive ~10-15 literal phrases, search FTS5 → top 50, with weights
  ├─ Semantic branch (new): embed query with same model → vector search → top 50 by cosine
  ├─ Merge: Reciprocal Rank Fusion, k=60: score = Σ 1/(k+rank) per branch — lexical win on exact IDs, semantic win on paraphrase, no tuning
  ├─ Rerank: cross-encoder (bge-reranker-base, ~110MB ONNX, or remote) scores query vs each of 100 candidates → top 20
  └─ Validate + dedup: run existing validate_citation() on each, 1 best per object, enforce evidence_limit (8-20)
```

Result is `EvidenceRetrieval` with same shape as today, just with an extra `retrieval_strategy: "hybrid-lexical-semantic-rerank-v1"` field. `--explain-retrieval` now shows lexical hits, semantic hits, fused score, rerank score.

Why this is smartest for large data:
- Lexical catches `contract #2024-1147` exactly
- Semantic catches `sales performed → revenue fell`
- RRF requires no weight tuning and degrades gracefully if one branch is empty
- Reranker fixes BM25 crowding (your digest-file problem)
- Incremental indexing fixes scale: `archiv add` only embeds new chunks, not all

### 3.2 Dual Mode — Exact Enforcement

You already built the right primitive in `model_adapter.py` + `evaluation_config.py`. Extend it from *model* to *retrieval*:

**Provider interface (one method each, like `ModelAdapter.complete`):**

```python
class EmbedProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def model_id(self) -> str: ...

class RerankProvider(Protocol):
    def rerank(self, query: str, passages: list[str]) -> list[float]: ...
```

Implementations: `LocalOnnxEmbed`, `RemoteOpenAIEmbed`, `LocalOnnxRerank`, `RemoteOpenAIRerank`. Each lives behind the same vault check:

```
Before any remote embed/rerank/complete:
  check_evaluation_opt_in(layout.root)  # raises with "run: archiv model evaluation enable --acknowledge..."
  decide_spend(prompt_or_texts, layout.root)  # raises SpendCeilingReached if over ceiling
  # only then open socket
```

`ModelConfig.adapter` grows from 3 to 5 values: `disabled | local-loopback | local-hybrid | remote-evaluation | remote-hybrid` — provenance still derived, never set by hand (existing test `test_provenance_is_derived_from_adapter_and_not_settable` catches bypass). A remote adapter with `endpoint` on `127.0.0.1` is still rejected — same test as today.

Every artifact stamped:

- `runs/ask/<id>/result.json`: `model.provenance`, `retrieval.strategy`, `embed_provider.model_id`
- DOCX report provenance section: already fixed in S06 to state true origin — extend to say `local-hybrid (bge-small, on this machine)` vs `remote-hybrid (text-embedding-3-small via api.openai.com, text was sent there on 2026-10-25)`

Blocked runs still leave `runs/ask/<id>/cost.json` with `refused_because` — your S07A/S07B pattern.

### 3.3 Institutional Layer — Minimal, Not a Platform

Do not build a server yet. Add 3 fields that turn a file archive into memory:

1. **Supersession:** `archiv supersede <new-sha> <old-sha> --reason "Board approved v2 on 2026-09-01"` → new table `supersessions(new_sha, old_sha, reason, created_at)`. Retrieval down-ranks superseded objects unless query says `include superseded`. Citation of superseded source carries warning in UI.
2. **Validity dates:** Optional `config/validity.json` per object: `valid_from, valid_until, authority (person/role)`. Retrieval boosts currently-valid docs. No deletion — advisory only.
3. **Access note:** `config/access.json` at vault root: `{"readers": ["auditor"], "writers": ["editor"]}` — enforced by CLI only on shared vaults. Honest limitation documented: OS-level, not crypto. Encryption-at-rest (age+scrypt, `archiv vault lock/unlock`) comes first so a stolen laptop vault is opaque.

These three use existing patterns: versioned JSON in `config/`, validated by Pydantic, backed up with the vault.

---

## 4. How Offline-Only and Online Both Work — Step by Step

**Offline vault** (`~/archiv-legal`, never marked):

```
archiv add ./contracts/          → hash, store original, derive segments, chunk+embed locally via ONNX
archiv ask "unpaid invoices?"    → lexical + local vector → fuse → local rerank → validate → build prompt with 8 passages → POST http://127.0.0.1:11434/api/chat (Ollama, loopback-only, tested before) → validate citations → stamped "from this machine"
# No network, no API key, no evaluation mark needed. Works on plane.
```

**Online vault** (`~/archiv-research`, marked):

```
archiv model evaluation enable --acknowledge-documents-leave-this-machine   # creates config/evaluation.json with timestamp+text
archiv model configure --adapter remote-hybrid --endpoint https://api.openai.com --model gpt-4o --embed-model text-embedding-3-small --api-key-env OPENAI_API_KEY --ceiling-usd 10 --prices prices.json
archiv add ./papers/             → same local chunk+embed, or remote embed if configured (still gated by ceiling)
archiv ask "unpaid invoices?"    → lexical + remote vector → fuse → remote rerank → validate → POST https://api.openai.com/v1/chat/completions + embed calls, all counted via cost_control → stamped "from gpt-4o via api.openai.com, text was sent there 2026-10-25, cost $0.04"
# If not marked or over ceiling → blocked_by_policy before any network, with exact remediation text
```

Same code path, different provider. The vault, not the user, decides.

---

## 5. Exact File / Table Changes

| Area | Add | Modify | Keep |
|---|---|---|---|
| Retrieval | `src/archiv/search/hybrid.py` (chunk, embed, fuse, rerank), `src/archiv/search/providers/local.py`, `.../remote.py` | `search/retrieval.py`: keep `derive_query_variants` as lexical branch, add hybrid entry `retrieve_evidence_hybrid()` | `search/index.py`, `search/schema.py` remain atomic rebuild |
| Index | `indexes/semantic.sqlite3` + `indexes/semantic/` for vectors if using file-per-shard | `rebuild_search_index(home, full=False)` gets incremental path | `indexes/search.sqlite3` stays FTS5 |
| Model/Provider | `src/archiv/retrieval_provider.py` (Embed+Rerank protocols) | `model_adapter.py`: add 2 adapter literals, `_validate_local_hybrid`, `_validate_remote_hybrid` | `evaluation_config.py` unchanged — single consent file gates everything |
| Cost | Extend `cost_control.SpendPolicy`: `embed_prices`, `rerank_prices` | `decide_spend()` counts embed tokens too | No default prices — still fails closed if not configured (your S07 rule) |
| Config | `config/embeddings.json` (pinned model path+sha256) | `config/model.json` gains embed/rerank fields | `config/evaluation.json` still the only consent switch |
| DB | `supersessions` table, `processing_queue` gets `embed_version`/`rerank_version` columns | Schema version 4→5, migration in `storage/database.py` (one BEGIN IMMEDIATE per step, your pattern) | `objects/ingestions/processing_runs` unchanged |
| CLI | `archiv rebuild-semantic-index`, `archiv embed status` | `archiv ask --explain-retrieval` adds semantic columns | `archiv add/find/source/report/doctor` behavior unchanged |

Dependencies: `onnxruntime` (already optional), `sqlite-vec` or `usearch` (MIT, no daemon) — added as optional extra `hybrid = [...]`, never required for base install.

---

## 6. Two Build Plans — Your Choice, Both Exact

### Plan A — Quick Win (6 weeks, single dev, one codebase, no server)

**Goal:** Make finding smart and dual mode honest — enough to validate institutional use.

| Week | Work | Done When |
|---|---|---|
| 1 | Pin embedding model (`bge-small-en-v1.5` ONNX, ~120MB) + `config/embeddings.json` + `LocalOnnxEmbed` + chunker (500/80) + test on synthetic fixtures (lawful only) | `tests/test_hybrid_chunking.py` passes, egress-denied run still passes (no download) |
| 2 | Vector table + incremental index + `rebuild-semantic-index --full` atomic | Adding 10 files to 10k does not re-hash 10k (timed) |
| 3 | Hybrid merge (RRF) + `retrieve_evidence_hybrid()` behind `--hybrid` flag, keep old as default | 22 frozen Qs still 22/22, real-corpus 10/20 → 16+/20 |
| 4 | Local reranker (bge-reranker-base ONNX) + `--explain-retrieval` with fused/rerank scores | Crowding digest files no longer dominate top-8 |
| 5 | Extend `ModelConfig` to `local-hybrid / remote-hybrid`, remote embed/rerank gated by `evaluation.json` + ceiling, stamped provenance | `test_remote_evaluation_*` + new `test_hybrid_provenance` pass, loopback boundary still holds |
| 6 | `supersede` command + validity note + docs + pilot on 2 real vaults (1 offline, 1 online) | Both vaults answer correctly, offline vault provably never contacted network (captured) |

**Risk:** Needs CPU for ONNX — document in `hardware-and-performance.md` with your HP Victus numbers. Falls back to lexical-only if ONNX absent (clean `skipped`, like OCR).

### Plan B — Full Institutional (6 months, after Plan A validated)

**Goal:** Team use without losing local-works-alone guarantee.

Build *only after* 2-3 institutions use Plan A and say "we need logins":

1. **Month 1-2:** Encryption-at-rest (`archiv vault lock/unlock` with age) + `doctor` integrity with encrypted originals
2. **Month 3-4:** Thin team server — stateless FastAPI that calls `archiv-core` as library, Postgres for `objects` metadata only, originals still content-addressed on disk, per-workspace `evaluation.json` policy
3. **Month 5-6:** Connectors (email `.eml`, Drive export, meeting VTT) as new `Extractor` entries — each with `magic` check before suffix, `limits.py` budget, lawful fixtures

**Risk:** Server doubles maintenance. Mitigation: server never owns data — `archiv backup` from server restores to laptop vault offline, proven in CI.

### Which to pick

- If you want **users this quarter** → Plan A. It is the exact solution to "smart finding + dual mode" with minimal surface.
- If target institutions say **"we need shared access controls"** → Plan B, but only then. Plan A is the prerequisite either way.

---

## 7. What Must Not Change — Ever

1. Originals immutable, content-addressed, read-only — rebuild never writes to `originals/`
2. Indexes rebuildable, excluded from backup integrity — `ARCHIV_HOME` moves only via verified `backup/restore`
3. Models propose, validators decide — `blocked_by_policy` is terminal, not retryable
4. Consent is a file with timestamp+text, not a boolean — corrupt/missing = not enabled
5. Every answer stamped with model+embed provenance — unstamped result cannot be produced (tested)
6. No runtime downloads — pinned file + sha256 or `skipped`
7. Fail closed on cost — no prices, no call; no ceiling, no call; tokens estimated from chars never

---

## 8. Next Action

Tell me **"Plan A"** or **"Plan B"** and I will queue it:

- Expand stubs into `docs/plan/steps/S28.md` (chunk+embed), `S29.md` (hybrid+RRF+rerank), `S30.md` (provider unification+stamping) with exact acceptance `pytest` targets in `queue.json`
- One PR per step, checks `ruff + pyright + pytest + doctor` green before merge, same gate you already enforce

If you disagree with any piece above, mark it and I correct it — that is the review contract.
