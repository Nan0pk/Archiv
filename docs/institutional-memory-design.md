# Institutional memory: design

Status: **draft for approval** (2026-11-07). Nothing below is built yet.

## 1. Intent

Archiv today finds text. The goal is that it **understands what an archive knows**.

The failure it must fix: someone starts work on something. The institution already
worked on the same thing before, under a different name, by different people, with
meetings and deliberation and maybe a decision to drop it. A search with today's
name finds nothing. The earlier work and the reasoning behind it are lost, and the
man-hours get spent again.

A grep-like tool fails in two ways:

1. **It can't see a thing under its other names.** That includes renames,
   abbreviations, transliterations and descriptions that never use the name.
2. **It can't bring the surroundings.** It doesn't return what is connected to the
   thing along any dimension: time, place, people and organisations, ideas, cause and
   effect, part and whole, and kinds of relationship nobody has listed.

**The product is a report.** The user asks in plain language and gets back a written
account of what the archive knows. Every statement is cited to the original page,
sheet or line. A visual map of the connections is a possible later showpiece, not
part of this work.

Success is a reader saying *"I didn't know we had already done that."*

## 2. Shape

```
question ──► ENTRY ──► WALK THE WEB ──► EXIT ──► cited report
             (model)    (no model)       (model)
                            ▲
                            │ built once, at ingest
                    THE WEB (graph + meaning links)
```

- **Entry.** A model reads the question and works out what it is about: names it
  might go by, related things, and starting points in the web.
- **The web.** It is built once per document at ingest and stored. It has three
  kinds of link:
  - **Things and their mentions.** A model reads each document and extracts the
    things in it: people, places, organisations, projects, ideas, events. It also
    extracts how they relate, in the model's own words, with the locator of the
    passage that says so.
  - **Same-thing merges.** A second pass decides which extracted things are the
    same across documents (see §4).
  - **Meaning links.** Every passage gets a meaning-vector, so passages about the
    same subject connect even when they share no words (see §5).
- **Walk.** Plain code, no model. It starts from the entry points and follows links
  of every kind outward, with limits on depth and size. It collects the passages
  along the way.
- **Exit.** A model reads what the walk collected and writes the report: what this
  is and what it has been called; where, when and with whom it appeared; what was
  tried and how it ended; and what nearby things the reader should know about.
  Every claim carries a citation back to the source location.

## 3. What is reused, what is replaced, what is left alone

**Reused as is:**
- **Ingestion and extraction:** every format, OCR, InPage and RTL, producing
  `derived/<sha256>/normalized/document.json` with segment locators.
- **Content-addressed originals** and the `objects` / `ingestions` tables.
- **The FTS5 index.** It stays as the exact-match channel. The walk can use it when
  the entry step produces literal names.

**Replaced:**
- **The reader inside `graph/builder.py`.** Today it is a regex
  (`[A-Z][a-z]+ [A-Z][a-z]+` = "person") plus hard-coded name blocklists. The model
  reader takes its place, and the regex and the blocklists are removed.

**New, kept small:**
- one module for building the web (extraction, merging, meaning-vectors)
- one module for the ask path (entry, walk, exit)
- one function that calls a model (§6)
- one CLI command to build and one to ask. *Builder's call:* these are new commands
  beside the existing `ask` and `report`, which stay as they are until the new path
  has proven better on blind probes.

**Left alone, not deleted:**
- contracts, executor, validators, MCP, cost control, hardware profiles, OCR
  benchmarks, Tk UI, and the existing person-centric graph queries
  (`CrossCorpusQueryResult` and friends)

The new path does not route through any of these and does not bend them to fit.
Whether to retire any of them is a separate later decision.

## 4. Merging the same thing across documents (the core problem)

A per-document reader makes one entry for each name it sees. An organisation under
its old name and under its new name become two unconnected entries. Without merging,
the web stays fragmented, and that is grep with extra steps.

Merging works in two stages:

1. **Propose.** Each extracted thing gets a vector from its name plus the passages
   that mention it. Nearby vectors, and similar names, become merge candidates. This
   is cheap and needs no model. The spike (§8) showed that meaning-vectors alone link
   a renamed organisation to its old name without being told.
2. **Confirm.** For each candidate pair, a model looks at a few passages from each
   side and answers whether they are the same thing, and why.
   - A confirmed merge keeps both names as aliases and cites the passages it was
     decided from.
   - A rejected pair stays separate.
   - Merges are stored separately from extractions, so they can be redone without
     re-reading documents.

## 5. Kinds of relationship come from the data

The current contracts fix the vocabulary in advance:
`NodeType` = person / location / organization / …, and
`RelationType` = appears_in / mentioned_in / co_occurs_with / associated_with.

That contradicts the goal, because the archive should reveal kinds of relationship
nobody listed. So:

- The model records each relationship **in its own words** ("replaced", "funded",
  "was a pilot for", "objected to"), verbatim, with its citation.
- An optional coarse bucket (time / place / actor / part-of / cause / other) can be
  attached for filtering. It is never the source of truth.
- The type and relation enums are not extended by hand. The new web uses free-text
  types with a coarse bucket.

Meaning links (passage ↔ passage by vector similarity) cover the relationships no
extraction caught.

## 6. Models

**During development (user's decision, 2026-11-07): no model-calling code in the
project.** The project's steps produce prompts and consume answers. Development and
test runs feed those prompts to Claude Haiku 5.5 at low effort through the
developer's existing Claude Code subscription (`claude -p`, with a short system
prompt, no tools and no MCP). Each run records token usage, so cost is known per
document and per MB before any bulk pass is planned.

Measured 2026-11-07: about 4.2K input and 5.3K output tokens for 4 short passages.
Output dominates the cost.

Haiku 5.5 at low effort scores about the same as the best ~27B open model at full
reasoning on a public capability index. That makes it a fair stand-in for the
eventual local model, a step stronger rather than far stronger.

**Later, when bulk runs or the product need it:** one function sends a prompt and
returns text. It reads the endpoint, model name
and key from a config file outside the repo. Switching between online and local is a
config change.

*Builder's call, veto-able:* this is a new small function. It is not the existing
`RemoteEvaluationAdapter`. That adapter hard-appends `/v1/chat/completions` to a
server-root endpoint and rejects any path, so it cannot reach Groq
(`…/openai/v1/…`) or Google's OpenAI-compatible route (`…/v1beta/openai/…`). Its own
comment says it has never sent a request to a real service. It is left untouched.

**Model choice: open-weight models that fit 24–32 GB VRAM**, so development and the
later local product use the same model.

| Use | Provider | Model |
|---|---|---|
| Bulk reading | Google AI Studio free tier | Gemma 4 (26B-A4B or 31B) |
| Entry / exit | Groq free tier | Qwen3.8-27B |

Check with the first key: whether Gemma 4 is reachable through Google's
OpenAI-compatible endpoint. Google's own Gemma example uses its `genai` SDK. If the
endpoint doesn't work, the one function gets a second small branch for that SDK.

**Cache every model output** keyed by `object_sha256` + model id + prompt version.
One full reading pass costs days of free quota, and models will be swapped. Changing
the model or the prompt re-reads only what changed.

**Data rule.** Free tiers may train on inputs: Google always does for Gemma, Mistral
does unless you opt out. Only the test corpus goes to online models, never real
institutional documents. The shipped product is local-first, with online as an
explicit opt-in.

**Quality note.** Results with ~30B models are what the local product will deliver.
Keep a short running note of where quality visibly depends on model size, so it is
known before launch.

## 7. Goals, non-goals, anti-goals

**Goals:**
- Find a thing under any of its names, including names never given to the system.
- Bring its surroundings along every kind of link.
- Report in plain language with checkable citations.
- Run on today's hardware for everything except the model calls.

**Non-goals for now:**
- a visual graph UI
- real-time ingestion
- multi-user features
- fine-tuning
- replacing ingestion or extraction

**Anti-goals** (failure even if it technically works):
- New layers, frameworks, contracts or governance around the new path.
- A relationship taxonomy or entity schema chosen up front.
- Any name, place or topic from a test corpus appearing in code, prompts or design.
  The system must not be shaped around examples.
- Routing the new path through the existing validators, executor or spend machinery.
- Evaluating with probes the builder picked because they would succeed.

## 8. Evidence so far, and how it gets tested

**Spike (2026-11-06), throwaway, outside the repo.** Read-only over an existing
archive of ~2,000 documents, deduplicated to 122k unique passages. A 220 MB
multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2` via
`fastembed`) took ~25 min on CPU. Blind test: 30 randomly drawn recent passages each
asked "what earlier passages are about the same thing?"
- **Found by meaning, missed by grep:** a renamed organisation linked to its old
  name; a "first time in years" claim contradicted by an earlier passage reporting
  the same event; recurring events across years with no shared vocabulary.
- **Weak:**
  - routine items match routine items
  - similarity below ~0.6 is mostly noise
  - near-duplicate wordings of one story surface separately
  - meaning-vectors give one undifferentiated "relatedness" and can't say why two
    things connect, which is the reason for §4–5

**Testing the real thing:**
- **Probes are drawn from the data at random**, not chosen by the builder.
- **The user judges a random sample of reports.** The judgement is whether the
  report tells them something real they couldn't have got from search.
- **Every report is compared against the FTS baseline** for the same question.
- **The current test corpus is a news digest.** It can test names, merging and
  surroundings. It **cannot** test recovering *what was deliberated and why it was
  dropped*, because that material isn't in it. A corpus with real deliberation
  (minutes, memos, proposals) is an open item for the user. It must not be fabricated.

## 9. Left to the builder

- Prompt wording, walk depth and size limits, and similarity thresholds, all tuned
  against blind probes.
- Storage layout: SQLite next to the existing indexes, and numpy files for vectors.
  No vector database at this scale.
- Embedding model choice within "multilingual, CPU-friendly".
- How passages are batched per model request to fit the free quotas.
