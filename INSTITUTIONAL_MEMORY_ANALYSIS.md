# Archiv as Institutional Memory: What Has to Change

**Date:** 2026-10-25 — Branch `arena/476a6970-archiv`  
**Status:** Analysis, not code. Read this before building.

---

## 0. Your prompt is not stupid

What you are asking is harder than it looks, which is why frontier models fail it:

> *You built Archiv as an offline-only, citation-backed archive so sensitive documents never leave the machine. Now you want the same codebase to also work for normal users who will happily use online models — without breaking the promise to sensitive users that nothing leaks.*

That is not a feature request. It is a trust problem with technical consequences. Getting it subtly wrong means either:
- a sensitive document is sent to OpenAI/Anthropic without clear consent, or
- the product is so locked down that normal teams will not use it.

Your instinct is right: the system must work in **two honest modes** — fully offline, and online-by-choice — and anyone reading an answer must be able to tell which produced it.

What you asked me to do:
1. Understand Archiv deeply, not superficially.
2. Tell you plainly what blocks it from being a real institutional memory.
3. Explain how to build the offline + online dual mode so both are trustworthy.
4. Give you two concrete plans to get there, in clear language.

---

## 1. Where Archiv is Today (Honest)

### What it already gets right for institutional memory

This is rare and worth protecting:

1. **Originals are untouched.** Every file is stored by its content hash, made read-only. You can always go back to what was actually filed.
2. **Answers carry receipts.** Citations point to an exact page, cell, slide, or line. The system checks before showing them that the original still exists and the text still matches. A made-up citation is rejected, not shown.
3. **Models propose, checkers decide.** A language model drafts an answer, but a separate deterministic check decides if it counts as success. This is the right shape for a knowledgebase people need to trust.
4. **No hidden network use — currently enforced.** Loopback-only to `127.0.0.1` is tested before any remote adapter existed (Phase A, S01). The egress-denied acceptance run proves it.
5. **Everything is rebuildable except originals.** Search indexes and derived text can be deleted and rebuilt. You will need this for long-lived archives.

### Why it is not an institutional memory yet

An institution needs more than good preservation. It needs shared use, over time, with turnover:

| Gap | Why it matters | Current state |
|---|---|---|
| **Single user on one Fedora machine** | Institutions have teams, not laptops | No users, no roles, no permissions per document |
| **Friction to install** | Normal staff will not run `curl \| bash` on Fedora | Tested only on Fedora; Windows/macOS not supported |
| **Search is literal** | People ask in different words than documents use | FTS5 exact-phrase + rule-based expansion only; no meaning-based search. 10/20 real-world questions already miss. |
| **No connectors** | Institutional knowledge lives in email, drives, meeting recordings, tickets — not just a folder of `docx` | Only file/folder ingest. `.wav` is catalogued, not transcribed. |
| **Silent content loss** | Tables, speaker notes, hidden cells, formulas, hyperlinks were dropped until very recently (S15-S18) | Mostly fixed now, but pattern shows fragility |
| **No notion of “still true”** | Old policy vs new policy, superseded reports, version conflicts | No versioning beyond hash deduplication; no “superseded by” link |
| **No access control or audit** | Who can see what? Who asked what? | SQLite has no row-level permissions; `diagnostics-export` is deliberately redacted — good for privacy, bad for compliance audits |
| **No encryption at rest** | A stolen laptop = stolen archive | Originals stored as plain files; no disk encryption managed by Archiv |
| **Scales to one machine** | 2,000 files in ~6 minutes; full index rebuild re-hashes every original | Single SQLite writer, no incremental index by default |
| **Report/answer quality not measured** | Citation exists ≠ claim is supported | Field notes: fabricated claim still passed “Verified Sources” check |

**Bottom line:** Archiv is a credible *personal evidence vault*. To be institutional memory, it needs team boundaries, meaning-aware search, living documents, and a dual-mode model layer — without losing the citation guarantee.

---

## 2. What “Institutional Memory” Actually Means Here

Not a vague vision. For Archiv, it means a team can:

1. **Keep** what matters — contracts, decisions, meeting notes, reports — without changing originals.
2. **Find** it later when the person who wrote it has left, even if they search in different words.
3. **Trust** the answer because every sentence points to a source they can open.
4. **Know if it is still current** — this policy replaced that one on this date, approved by this person.
5. **Control who sees what** — finance files not visible to interns; sensitive vault never leaves that machine.
6. **Survive hardware/people change** — backup, restore, hardware migration without silent corruption.

And do it whether their vault is offline-only (sensitive) or online-assisted (general).

---

## 3. The Dual-Mode Design That Makes Both Trustworthy

This is the one decision that makes everything else safe.

Do **not** make “online vs offline” a hidden setting. Make it a visible property of each **vault**, with a physical marker on disk:

```
Vault A (offline-only)          Vault B (online-allowed)
ARCHIV_HOME = ~/archiv-legal    ARCHIV_HOME = ~/archiv-marketing
config/evaluation.json = absent  config/evaluation.json = present + timestamp + exact consent text
adapter = openai-compatible-loopback  adapter = remote-evaluation over HTTPS
Model runs on 127.0.0.1           Model is api.openai.com (or any OpenAI-compatible endpoint)
Nothing leaves the machine        Text of retrieved passages leaves; never raw originals unless needed
Answer stamped: “from this machine”  Answer stamped: “from external model X on [date], text was sent there”
```

**How you already started this correctly:** `src/archiv/evaluation_config.py` + `model_adapter.py` in Phase A do exactly this — a `provenance` field derived from `adapter`, not set by hand; a raising `check_evaluation_opt_in()` that names the exact command to enable it. Keep that pattern.

Rules to add so sensitive users stay safe even when both modes exist in one product:

1. **Default is offline.** A new vault has no remote adapter. No API key, no endpoint, no network.
2. **Consent is per-vault and recorded.** The `evaluation.json` file stores *when* and *what* was acknowledged, not just `true`. Environment-variable override (`ARCHIV_EVALUATION_ARCHIVE=1`) is allowed only for tests and is recorded as such.
3. **Network call is refused before any retrieval if the marker is missing.** Not after the prompt is built. Both `ask` (grounding.py) and `report` (tasks.py) already do this — keep the duplication, do not merge them into one helper that could be forgotten.
4. **What is sent is minimal and logged.** Only the retrieved evidence passages + query are sent to the remote model, not whole documents. The retry loop + cost ledger (`cost_control.py`) already logs spend; extend it to log *what left*.
5. **Every output is stamped.** `report` DOCX provenance section + `ask` result JSON already carry `provenance`. An unstamped result cannot exist (existing test `test_no_remote_ask_can_produce_an_unstamped_result`).
6. **No online fallback.** `DisabledModelAdapter` raises, never tries something else. This stays.

**For online users, also add:**
- Spend ceiling checked *before* the network call (you have this — S07).
- Token counting from a pinned tokenizer file, never a download at runtime (you have this design, finish the tokenizer pinning).
- Model output must match a JSON schema; if the remote provider silently drops enforcement, fail loudly, not silently (S04A).

---

## 4. Two Plans — Pick One

### Plan 1: The Pragmatist — Extend the Vault

**Idea:** Keep one product, one codebase, one trust model. Add the minimum needed so a normal team can use it this year.

**Best if:** You want to ship to real users fast, with one thing to maintain, and you are okay with “team use = shared drive + separate vaults” rather than a true multi-user server.

**Phases (6–9 months, in order):**

**Phase 1 — Make search useful (8 weeks)**
- Keep FTS5 as the fast, offline default.
- Add an **optional** meaning-based index alongside it (embeddings, stored locally, using a pinned open-weight model — no download at runtime). It runs in the “deep tier” queue you already designed (`processing_queue` table) so `archiv add` stays fast.
- Retrieval merges: literal hits first (exact citations), then meaning hits to catch paraphrase. Both are citation-validated the same way.
- **Measure:** 22-question frozen benchmark + new real-world corpus set; report recall@k honestly, not with invented scores.

**Phase 2 — Make it installable and team-practical (6 weeks)**
- Support Windows + macOS (ingestion already POSIX-only for Excel sandbox — document and gate it).
- `archiv vault create/list/share` — a vault is just an `ARCHIV_HOME` with a name. Sharing = verified backup + restore on another machine (you already have `backup`/`restore` with hash-checked manifest). Do not build sync yet; shared drive + backup is enough for small institutions.
- Per-vault access note: a plain `access.json` listing allowed OS users (read/write/admin). Enforced by CLI, not by crypto — good enough for trusted machines, and honest about limits.

**Phase 3 — Finish dual-mode properly (6 weeks)**
- `archiv model configure --offline ollama` vs `archiv model configure --online openai --model gpt-4o-mini --api-key-env OPENAI_API_KEY --ceiling-usd 5.00 --prices ...`
- Offline vault: loopback only. Online vault: HTTPS only, requires `evaluation.json` mark + ceiling + prices; fails closed otherwise.
- UI shows at startup: `Vault: marketing (online — text sent to api.openai.com, ceiling $5)` vs `Vault: legal (offline — nothing leaves this machine)`.

**Phase 4 — Institutional basics (8 weeks)**
- **Lifecycle:** `archiv supersede <new-sha> <old-sha> --reason "..."` — does not delete original, just links “this replaces that.” Answers prefer non-superseded sources; superseded citations are flagged.
- **Ingestion connectors (one at a time, each a new Extractor):** email export (`.eml`/`.mbox`), meeting transcripts (`.vtt`/`.srt` already close to `.wav` transcription you planned in S20), Google Drive export. Each follows existing `Extractor` + `limits.py` pattern.
- **Retention marker:** `config/retention.json` — “keep board minutes forever, keep drafts 2 years” — advisory only; deletion still requires explicit command so nothing disappears silently.

**What you get:** A team can install on any OS, keep sensitive vaults offline, run general vaults with online models, search by meaning, and prove where every answer came from. No server to run.

**What you do not get:** Real concurrent multi-user editing, row-level crypto, or automatic sync. For many institutions, that is fine for 1–2 years.

**Risk:** Shared-drive sharing is clumsy. If institutions demand true multi-user sooner, you will rebuild this. That is why Plan 2 exists.

---

### Plan 2: The Product Split — Local Vault + Optional Team Service

**Idea:** Separate two concerns that want to be separate: **preservation** (must be offline, simple, eternal) and **collaboration** (needs a server, users, permissions, but can be online).

**Best if:** Your target is organisations that need proper permissions, audit, and many concurrent users — law firms, NGOs, universities — and you are willing to run a hosted service or let them self-host one.

**Two components that share one core:**

```
archiv-core (library)  ←  you already have this; it is src/archiv/
    │
    ├── archiv-vault (CLI + desktop)  ← today's product, stays offline-first
    │       single-user, FTS + optional local embeddings
    │       backup/restore is the sync mechanism
    │       “institutional memory for individuals and small teams”
    │
    └── archiv-team (server)  ← new, thin wrapper around archiv-core
            multi-user, Postgres (not SQLite) for the metadata layer
            real login, roles (reader / contributor / approver / admin)
            per-document visibility (team / department / private)
            audit log: who ingested what, who asked what, when superseded
            runs archiv-core's validation unchanged — server cannot invent citations
```

**How offline vs online works here:**

| User type | Vault location | Model | Data leaves? |
|---|---|---|---|
| Individual with sensitive data | Their laptop (`ARCHIV_HOME`) | Local Ollama | No |
| Individual with general data | Their laptop, marked | Online provider of their choice | Only evidence passages, with consent |
| Team member | Team server (self-hosted or yours) | Server’s configured provider (offline or online per workspace) | Per workspace policy: “legal workspace = offline only” vs “marketing workspace = online allowed” |

The key is **workspace policy**, not user preference. An intern cannot switch the legal workspace to online; only an admin with the vault key can change `evaluation.json`.

**Implementation order:**

1. **Core stays pure.** No import of web frameworks, auth, or network into `src/archiv/` except `model_adapter.py`’s single `complete()` method. This is the promise that the vault can always run alone if the server disappears.
2. **Add encryption at rest to core.** Before any server exists, vault originals are encrypted with a key derived from a passphrase (e.g., age + scrypt). `archiv vault unlock` decrypts in memory only. This is not a server feature — it is what makes a laptop vault safe from theft, which institutions will ask for first.
3. **Build the server as a stateless API in front of core.** Every server operation calls the same `ingest_file`, `retrieve_evidence`, `run_grounded_ask`, `validate_citation` you already have. The server stores *its own* `archiv.sqlite3` per workspace (or Postgres for concurrency) but originals and derived evidence keep the same layout — so `archiv backup` from the server can be restored into a local vault offline, and vice versa.
4. **Auth and permissions are server-only.** Core never learns about users. The server maps `user → role → workspace → visibility` and enforces it before calling core. Audit log is a server table, not part of core, so a local vault has no “who asked what” record to leak.
5. **Ingestion connectors live on the server.** Email, Drive, Slack ingest run as server jobs (same queue pattern, same limits). Local vault keeps file/folder ingest only.

**What you get:** Proper institutional memory — permissions, audit, concurrent use, policy-per-workspace online/offline control, and a local vault that still works with no server at all. The two share citation guarantees because they share code.

**What you do not get:** Simplicity. Two components, two install paths, auth to maintain. Do not start here unless you have a design partner institution willing to run the server with you.

**Risk:** You build a server no one wanted. Mitigate by only building it after Plan 1 has 3–5 real institutions using vaults and explicitly asking for shared access.

---

## 5. My Recommendation

Do **Plan 1 first, with Plan 2’s core hygiene.**

Specifically:

1. Before adding users or a server, add **vault encryption at rest**. It is the most requested institutional feature and the hardest to bolt on later. One passphrase, one key file, plain files on disk become opaque. Do it while you still only have SQLite and one writer.

2. Then do Plan 1’s Phase 1 and 3 in parallel: meaning-based search (local, optional, queued) and the proper offline/online vault distinction. These are the two things that make the product *useful* (search) and *honest* (dual mode).

3. Run Plan 1 with 2–3 pilot institutions where one vault holds genuinely sensitive material (stays offline) and another holds general material (uses online model). Their feedback tells you whether Plan 2 is needed or whether backup/restore sharing is enough.

Do not build a multi-user server because institutional memory “should” have one. Build it only when institutions using Plan 1 hit a wall that backup/restore cannot fix.

---

## 6. What Must Not Change Regardless

- Originals stay immutable, content-addressed, read-only.
- Search indexes and derived text stay rebuildable.
- Models propose, validators decide — never the other way.
- A vault that has not been explicitly marked for online use cannot send anything, even if an API key is set. The marker is the consent; the adapter check is the enforcement.
- Every answer is stamped with where its words came from.
- Fail closed. A missing tokenizer is not estimated. A missing ceiling is not bypassed. A missing original is not faked.

---

*Next step if you want to proceed:* Pick Plan 1 or Plan 2 in reply and I will turn the chosen phase 1 into queued steps (`docs/plan/steps/*.md` + `queue.json`) with acceptance checks, so work can start in the normal one-step-per-PR flow.
