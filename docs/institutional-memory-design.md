# Institutional memory: design

Status: **approved by the owner** (2026-11-07). Built so far: everything in sections 2 to 5,
as the `archiv memory` commands. Not yet done: the owner's judgement of real reports against
plain search, which decides whether the old regex reader is removed (plan, Task 9).

> **For anyone joining this work.** Examples you hear in discussions are
> illustrations, never requirements. Do not shape code, prompts, tests or this
> document around any single scenario, corpus, topic or name. If a change only makes
> sense for one kind of question or one collection, it is the wrong change.

## 1. Intent

Archiv today finds text. The goal is that it **understands a collection**: what
things it contains, what each thing is called in all its different ways, and how
things connect to one another. It should then answer questions with that
understanding instead of matching strings.

Search fails in two general ways:

1. **Vocabulary.** The same thing appears under different names, spellings,
   abbreviations, languages or descriptions. A search for one form misses the rest.
2. **Context.** Knowing one thing means knowing what it connects to. Search returns
   matches, not connections, and it can't know what is relevant without matching
   words.

Connections run along any dimension the material holds. Nobody, including the
builder, lists those dimensions in advance. The system has to discover them from
the documents.

**The product is a report.** The user asks in plain language and gets back a written,
organised account of what the collection knows that bears on the question. Every
statement is cited to the original page, sheet or line. A visual map of the
connections is a possible later showpiece, not part of this work.

**Success:**
- The report gives the reader relevant knowledge from the collection that they could
  not reasonably have assembled with search.
- Every claim in it checks out against its cited source.

## 2. Shape

```
question ──► ENTRY ──► WALK THE WEB ──► EXIT ──► cited report
             (model)    (no model)       (model)
                            ▲
                            │ built once, at ingest
                    THE WEB (graph + meaning links)
```

- **Entry.** A model reads the question and works out what it is about: the forms
  the subject may take in the collection, related things, and starting points in
  the web.
- **The web.** It is built once per document at ingest and stored. It has three
  kinds of link:
  - **Things and their mentions.** A model reads each document and extracts
    whatever things it mentions, of any kind. It also extracts the relationships
    the text states between them, in the model's own words, each with the locator
    of the passage that says so.
  - **Same-thing merges.** A second pass decides which extracted things are the
    same across documents (§4).
  - **Meaning links.** Every passage gets a meaning-vector, so passages on the same
    subject connect even when they share no words (§4, §8).
- **Walk.** Plain code, no model. It starts from the entry points and follows links
  of every kind outward, with limits on depth and size. It collects the passages
  along the way.
- **Exit.** A model reads what the walk collected and writes the report. The report
  is organised around whatever the material shows matters for the question. It is
  not a fixed template. Every claim carries a citation back to the source location.

## 3. What is reused, what is replaced, what is left alone

**Reused as is:**
- **Ingestion and extraction:** every format, OCR, InPage and RTL, producing
  `derived/<sha256>/normalized/document.json` with segment locators.
- **Content-addressed originals** and the `objects` / `ingestions` tables.
- **The FTS5 index.** It stays as the exact-match channel. The walk can use it when
  the entry step produces literal forms.

**Replaced:**
- **The reader inside `graph/builder.py`.** Today it is a regex
  (`[A-Z][a-z]+ [A-Z][a-z]+` = "person") plus hard-coded name blocklists. The model
  reader takes its place, and the regex and the blocklists are removed.

**New, kept small:**
- one module for building the web (extraction, merging, meaning-vectors)
- one module for the ask path (entry, walk, exit)
- one CLI command to build and one to ask. *Builder's call:* these are new commands
  beside the existing `ask` and `report`, which stay as they are until the new path
  has proven better on blind probes.
- no model-calling code for now (§6)

**Left alone, not deleted:**
- contracts, executor, validators, MCP, cost control, hardware profiles, OCR
  benchmarks, Tk UI, and the existing person-centric graph queries
  (`CrossCorpusQueryResult` and friends)

The new path does not route through any of these and does not bend them to fit.
Whether to retire any of them is a separate later decision.

## 4. Merging the same thing across documents (the core problem)

A per-document reader makes one entry per form it sees. The same thing written
differently in different documents becomes several unconnected entries. Without
merging, the web stays fragmented, and that is search with extra steps.

Merging works in two stages:

1. **Propose.** Each extracted thing gets a vector from its name plus the passages
   that mention it. Nearby vectors, and similar forms, become merge candidates.
   This is cheap and needs no model.
2. **Confirm.** For each candidate pair, a model looks at a few passages from each
   side and answers whether they are the same thing, and why.
   - A confirmed merge keeps every form as an alias and cites the passages it was
     decided from.
   - A rejected pair stays separate.
   - Merges are stored separately from extractions, so they can be redone without
     re-reading documents.

## 5. Kinds of things and relationships come from the data

The current contracts fix the vocabulary in advance:
`NodeType` = person / location / organization / …, and
`RelationType` = appears_in / mentioned_in / co_occurs_with / associated_with.

That contradicts the goal. So in the new web:

- **Kinds of things and relationships are free text**, recorded verbatim as the
  model describes them, with their citation.
- **No fixed list of kinds**, coarse or fine, is defined by hand. If grouping is
  needed later, for filtering or display, it is derived from the recorded phrases
  themselves, for example by clustering them.
- **The old enums are not extended.**

Meaning links (passage ↔ passage) cover the relationships no extraction caught.

## 6. Models

**During development (user's decision, 2026-11-07): no model-calling code in the
project.** The project's steps produce prompts and consume answers. Development and
test runs feed those prompts to Claude Haiku 5.5 at low effort through the
developer's existing Claude Code subscription (`claude -p`, with a short system
prompt, no tools and no MCP). Each run records token usage, so cost is known per
document and per MB before any bulk pass is planned. Measured: output tokens
dominate the cost, not input.

Haiku 5.5 at low effort scores about the same as the best ~27B open model at full
reasoning on a public capability index. That makes it a fair stand-in for the
eventual local model, a step stronger rather than far stronger.

**Bulk passes and the product.** When a whole corpus has to be read, or the product
needs a model, add one function that sends a prompt and returns text. Its endpoint,
model and key come from a config file outside the repo, and switching between
online and local is a config change. Prefer open-weight models that fit 24–32 GB
VRAM, so what works in development is what runs locally later. Which provider to use
is decided at that point, from what is verified to work then. Provider limits change
too often to fix here.

*Builder's call, veto-able:* that function is new and small. It is not the existing
`RemoteEvaluationAdapter`. That adapter hard-appends `/v1/chat/completions` to a
server-root endpoint and rejects any path, so it cannot reach most hosted providers.
Its own comment says it has never sent a request to a real service. It is left
untouched.

**Cache every model output** keyed by `object_sha256` + model id + prompt version.
Changing the model or the prompt re-reads only what changed.

**Data rule.** Free tiers of hosted models may train on inputs. Only test corpora go
to online models, never real institutional documents. The shipped product is
local-first, with online as an explicit opt-in.

**Quality note.** Keep a short running note of where quality visibly depends on
model size, so it is known before launch.

## 7. Goals, non-goals, anti-goals

**Goals:**
- Recognise a thing in all the forms it takes in a collection, including forms
  nobody told the system about.
- Connect things along whatever dimensions the material holds.
- Answer in plain language with checkable citations.
- Run on today's hardware for everything except the model calls.

**Non-goals for now:**
- a visual graph UI
- real-time ingestion
- multi-user features
- fine-tuning
- replacing ingestion or extraction

**Anti-goals** (failure even if it technically works):
- New layers, frameworks, contracts or governance around the new path.
- Kinds of things or relationships chosen up front, at any granularity.
- Designing for one scenario, question type, corpus, topic or name. Nothing from a
  test corpus appears in code, prompts or design documents.
- Routing the new path through the existing validators, executor or spend machinery.
- Evaluating with probes that the builder picked.

## 8. Evidence so far, and how it gets tested

**Spike (2026-11-06), throwaway, outside the repo.**
- Read-only over an existing test archive of ~2,000 documents, deduplicated to 122k
  unique passages.
- A 220 MB multilingual embedding model on CPU took ~25 min.
- Blind test: 30 randomly drawn passages were each matched by meaning against the
  rest of the archive.

Results:
- **Meaning matching found related passages that shared no vocabulary with the
  probe**, which search could not find. That included the same entity under
  different names.
- **Weak:**
  - routine items match routine items
  - similarity below ~0.6 is mostly noise
  - near-duplicate wordings surface separately
  - vectors give one undifferentiated "relatedness" and can't say *why* two things
    connect, which is the reason for §4–5

**Testing the real thing:**
- **Probes are drawn from the data at random**, never chosen by the builder.
- **The user judges a random sample of reports** against the success test in §1.
- **Every report is compared against the FTS baseline** for the same question.
- **One test corpus covers one genre.** It can't show how the system behaves on other
  kinds of records. Further real corpora of different kinds are an open item. Test
  material is never fabricated.

## 9. Left to the builder

- Prompt wording, walk depth and size limits, and similarity thresholds, all tuned
  against blind probes.
- Storage layout: SQLite next to the existing indexes, and numpy files for vectors.
  No vector database at this scale.
- Embedding model choice within "multilingual, CPU-friendly".
- How passages are batched per model request.
