# Institutional memory: design

Status, in three parts:

1. **What the owner approved (2026-11-07):** the design in which a model reads every document
   at ingest. Not yet done under it: the owner's judgement of real reports against plain
   search, which decides whether the old regex reader is removed (plan, Task 9).
2. **What changed since, decided by the builder (2026-11-07).** The owner said "you decide"
   and has not judged the evidence. Reading every document with a model at ingest is too slow
   for the offline product: about 1.8 minutes and 29 thousand written tokens per document, so
   100,000 documents is about 1.8 years on one local graphics card. Sections 2, 4, 5 and 6
   below describe the replacement: **no model reads documents at ingest.** The reason is
   ingest cost. Quality on 20 documents was a wash and the open risk is finding other forms
   of a name at scale (section 8).
3. **What exists in the code today:** the model-reads-every-document version, as the
   `archiv memory` commands. The replacement exists only as throwaway test scripts outside
   the repository.

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
                            │ built once, at ingest, with no model
                    THE WEB (names + meaning links)
```

- **Entry.** A model reads the question and works out what it is about: the forms
  the subject may take in the collection, related things, and starting points in
  the web.
- **The web.** It is built once per document at ingest and stored, with **no model
  reading the documents**. It has three kinds of link:
  - **Names and where they appear.** Ordinary language tools pick out the noun
    phrases and named things in every passage. No list of kinds is kept. Two names
    in the same passage are linked as "appears with". Names that appear in a large
    share of the collection are left out as links, because they connect everything
    to everything.
  - **Same-thing merges.** Decided at question time, for the names the question is
    about (§4).
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

1. **Propose.** Each name gets a vector from its text plus the passages that mention
   it. Nearby vectors, and similar forms, become merge candidates. This needs no model.
2. **Confirm.** For each candidate pair, a model looks at a few passages from each
   side and answers whether they are the same thing, and why.
   - A confirmed merge keeps every form as an alias and cites the passages it was
     decided from.
   - A rejected pair stays separate.
   - Merges are stored separately from names, so they can be redone without
     re-reading documents.

**Where the two stages run (changed 2026-11-07).** Run over a whole collection at ingest,
both stages cost too much. Measured on 6,136 passages: finding candidates took 621
seconds (about a tenth of a second per passage, growing in step with the collection),
produced 5,595 candidates, and confirming one pair takes about 4.5 seconds of model time.
Confirming them all is about 7 hours, longer than having a model read the 100 documents.
Of those 621 seconds, a sampled estimate puts about 440 on turning every one of the
34,360 names into a vector and about 145 on the neighbour search. So the plan is:
neither stage runs over the whole collection at ingest, and **no vectors of names are
built at ingest**. The passage vectors already exist (56 seconds for the same 100
documents). At question time, the proposal takes the names the entry step produced, finds
the passages nearest to them by the existing passage vectors, and offers as candidates the
names in those passages whose text looks like a form of the same name (shared words,
one containing the other, near spelling). The confirmation covers only those few pairs.
Confirmed merges are stored and reused. **This is not built or tested yet**; until it is,
the existing capped ingest-time confirmation is the only version that exists.

## 5. Kinds of things and relationships come from the data

The current contracts fix the vocabulary in advance:
`NodeType` = person / location / organization / …, and
`RelationType` = appears_in / mentioned_in / co_occurs_with / associated_with.

That contradicts the goal. So in the new web:

- **At ingest there are no kinds and one relationship**: "appears with", meaning two
  names share a passage. Anything richer is stated by the model at question time, in
  its own words, from the passages it is shown, each with its citation.
- **Kinds of things and relationships are free text** wherever they are recorded,
  verbatim as the model describes them, with their citation.
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

**Where the model is used (changed 2026-11-07).** At question time only: the entry
step, the same-thing check for the names the question is about, and the report. Ingest
needs no model. The model-reads-every-document version stays in the code as the
slow, higher-detail option and as the comparison for tests.

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

**Model-built against cheap-built memory (2026-11-07), 20 documents.** The same code
and the same 10 random questions, once on a memory a model built by reading the documents
and once on a memory built by ordinary language tools with no model. One shared
first-step answer per question.
- Passages containing the exact words that were reported: 34 of 34 (model-built), 33 of 34
  (cheap).
- Finding passages that use only another form of the name (17 passages, in 6 pairs of
  name forms; an earlier version of this line wrongly said 17 cases): walking the memory
  alone from one starting name, 12 (model-built) against 4 (cheap) passages handed over.
  Through the whole question path, 13 against 16 passages cited. The two numbers count
  different things. The gain for the cheap memory on the whole path most likely comes
  from the entry step, which offers other forms of the name after seeing the 5 nearest
  passages; the walk alone already used the passage vectors and still found only 4. That
  explanation was not tested separately.
- About 14 seconds a question (model-built) against 9 (cheap).
- The owner judged the first round of this comparison by feel: mostly ties. The second round
  was judged by the builder, not blind, and is not counted as evidence.
- Limits: one try per question, one genre (English daily news digests), and the same model
  writes both reports.

**Cheap memory on 100 documents (6,136 passages), no model reading anything.**
- Embedding the passages 56 seconds, finding names 6 seconds, building the memory 3
  seconds (34,360 names, 258,141 "appears with" links, 25 over-common names left out).
- Finding merge candidates: 621 seconds, 5,595 candidates (see section 4 for why this
  moves to question time). Two sizes were measured (about 500 passages and 6,136), and the
  time grew in step with the passages. If it keeps doing so, 100,000 documents is about a
  week.
- Ten random questions through the whole path: about 15 seconds each. The walk itself is
  under a second of that; the rest is the two model calls. Passages with the exact
  words that were reported: 35 of 59. One question was a date that 30 passages contain, more
  than a report can be given, and the report said so (8 given of 30); leaving that one out it
  is 27 of 29. Both missed passages belong to one question about an oddly hyphenated name;
  why they were missed was not looked into.

**Faster meaning vectors (2026-11-08), 20 documents.** This laptop has no graphics card, so
the vectors are a pure processor limit. The current model makes about 130 passages a second
using about 16 of 24 cores; 6 arrangements of threads and processes were tried and none beat
it, so 6 million passages is about 13 hours. A word-averaging ("static") model that ships
with the same library, `minishlab/potion-multilingual-128M`, makes about 3,267 a second, 25
times faster, about 30 minutes for 6 million. It was compared on the same cheap memory, with
its similarity cut-offs set so that as many passages pass as pass with the current model:
- Passages containing the exact words that were reported: 33 of 34 with each.
- Finding passages that use only another form of the name (the 17 passages in 6 pairs):
  walking the memory alone, 17 with the fast model against 4 with the current one; through
  the whole question path, 17 against 16. The fast model matches shared word pieces ("mkt"
  with "market"), which suits spelling and abbreviation variants.
- **What it loses.** For 40 random passages, the best-matching other passage that shares no
  word of five letters or more was judged by a model (once, in random order) to be about the
  same subject: 4 of 40 with the current model, 0 of 40 with the fast one. So it gives up
  most of what made meaning vectors worth having, though the current model found it only
  one time in ten on this collection.
- Limits: the current-model results were replayed from stored answers of the earlier run,
  so the first-step answer differed between the two; I set the cut-offs; one try; one genre;
  the judge is a model.
- **Across languages.** For 40 random Arabic-script passages (from the 2,004-document
  archive, where such text is only 0.25% of the passages), the best-matching English
  passage out of 20,000 was judged by a model, once, in random order, to be about the same
  subject: 5 of 40 with the current model, 0 of 40 with the fast one.
- **What the alias test cannot show.** All 6 name pairs in it share word pieces ("mkt" and
  "market", "majed" and "majid"), which is exactly what a word-averaging model is good at.
  It says nothing about acronyms against full names, translations, or different names for
  one thing. Those depend on the ability that dropped.
- **Decision (builder, under "you decide"):** the fast model is **not** the default. The
  goal names languages and meaning across wording, and on both checks it found nothing
  where the current model found something (rarely, but not zero). It is offered as an
  explicit setting for collections that are in one language and where speed matters most,
  with these limits stated. The current model stays the default until there is a design
  that gets both (for example fast vectors first so the collection can be used within
  minutes, and the slower vectors filled in behind them). Not built yet.

**Not tested yet:**
- Acronyms against full names, and other variants that share no word pieces, with either
  model.
- Finding other forms of a name at 100 documents. The known cases are not in that set, and
  the one that is has 44 matching passages against 40 report slots, so it proves nothing.
- Merging at question time (section 4). It does not exist yet.
- Text that is not English. The language tools are English; a language-independent method
  exists but was noisy on Arabic-script text.
- Each question loads every name in the memory. That costs under a second at 34,360 names;
  it has not been measured at 100,000 documents.

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
