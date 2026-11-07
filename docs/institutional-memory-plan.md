# Institutional memory: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the "web" of a collection (things, their forms, relationships and meaning
links) at ingest time, and answer plain-language questions with a cited report walked
from that web.

**Architecture:** A new package, `archiv.memory`, reads passages from the existing search
index. Through an injected `complete(prompt) -> str` callable, it has a model extract
things and relationships, caching every model output. It embeds passages and things with
a CPU embedding model, proposes merges by vector and has the model confirm them, and
answers questions with entry (model), walk (plain code) and exit (model) steps. Its state
lives in `indexes/memory.sqlite3` plus numpy files beside it. Nothing in the existing
graph, contracts, validators or executor is touched until Task 9.

**Tech stack:** Python 3.12, sqlite3, numpy, fastembed (CPU ONNX), Typer, pytest.

**Spec:** `docs/institutional-memory-design.md`

## Global constraints

- **Python and checks:** `requires-python >=3.12`. Ruff `line-length = 100`. Run
  `ruff format --check .`, `ruff check .`, `pyright --pythonpath .venv/bin/python` and
  `pytest -q` before every commit.
- **Dependencies:** `numpy` and `fastembed` go in a new optional extra `memory`. Core
  install stays unchanged. Tests must not need the extra or a network (see the
  `HashEmbedder` fake in Task 4).
- **No model-calling code in the repo.** The only model surface is
  `Complete = Callable[[str], str]`, plus `command_completer`, which pipes a prompt to
  a user-given command on stdin and returns its stdout. No provider names, endpoints or
  keys appear anywhere in `src/` or `tests/`.
- **Free text, no lists of kinds.** Kinds of things and relationships are stored as the
  model wrote them. There is no enum, mapping table or allow-list of kinds anywhere.
- **Nothing from any test corpus** (names, topics, abbreviations) appears in code,
  prompts, tests or docs. Test fixtures use neutral invented text.
- **Cache key** for every model output: `sha256(object_or_pair_key + model_label +
  prompt_version + prompt)`. `model_label` comes from the CLI option `--model-label`.
- **Citations:** every stored mention, relation and merge carries `segment_id`s, and
  every report claim cites passage ids that exist in the walked set.
- **New CLI commands must appear in `README.md`.** `test_documentation_coverage.py`
  enforces this.
- **This work stays outside the `docs/plan/` queue** (user's decision; see design §3).
  Do not edit `docs/plan/*`, `queue.json` or status files.

## Review focus

1. **The model returns non-JSON, fenced JSON, leading reasoning text, or truncated
   JSON.** Parse the first complete JSON object. If there is none, count the batch as
   failed and let the build continue. Pinned in Task 2.
2. **The model cites a passage id that is not in its batch, or names a thing never
   given a mention.** That item is dropped and counted, never stored. Pinned in Task 2.
3. **Non-Latin, RTL or mixed-script forms**, and differences in Unicode composition.
   Forms are stored verbatim. Identity uses NFKC + casefold + collapsed whitespace, so
   two compositions of one name are one thing. Pinned in Task 3.
4. **A re-run after an interruption or with unchanged inputs makes zero model calls.**
   Changing `--model-label` or a prompt version causes re-calls. Pinned in Task 3.
5. **A question that matches nothing** produces the fixed text "The collection has
   nothing that bears on this question." with no model exit call and no invented
   content. Pinned in Task 6.

---

### Task 1: Passages and the model surface

**Files:**
- Create: `src/archiv/memory/__init__.py`, `src/archiv/memory/passages.py`,
  `src/archiv/memory/model.py`
- Modify: `pyproject.toml` (add the `memory = ["numpy>=2", "fastembed>=0.5"]` extra)
- Test: `tests/test_memory_passages.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Passage: segment_id: str; object_sha256: str;
    source_name: str; locator: dict[str, object]; text: str`
  - `def load_passages(home: Path | None = None, *, min_chars: int = 40) -> list[Passage]`
    reads `segments` from `search_index_path(layout)` read-only (`file:…?mode=ro`). It
    drops texts shorter than `min_chars` after whitespace collapse, and keeps the first
    passage per sha256 of the collapsed, casefolded text. Order is
    `(object_sha256, segment_index)`.
  - `def passages_by_object(passages: list[Passage]) -> dict[str, list[Passage]]`
  - `Complete = Callable[[str], str]`
  - `def command_completer(argv: list[str], *, timeout_s: int = 900) -> Complete` runs
    `subprocess.run(argv, input=prompt, capture_output=True, text=True, check=True,
    timeout=timeout_s)` and returns stdout.

- [ ] **Step 1: Write failing tests.**
  - `test_load_passages_dedupes_and_filters`: ingest two `.txt` files with neutral
    text. One line repeats verbatim across both files, and one line has 10 characters.
    Assert the repeated text appears once and the short line is absent.
  - `test_load_passages_requires_index`: on an empty home it raises
    `FileNotFoundError`.
  - `test_command_completer_pipes_stdin`:
    `command_completer([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"])("abc")`
    returns `"ABC\n"`.
- [ ] **Step 2:** Run `pytest tests/test_memory_passages.py -v`. Expect FAIL (import
  error).
- [ ] **Step 3:** Implement the interfaces above.
- [ ] **Step 4:** Run the tests. Expect PASS. Run the full checks.
- [ ] **Step 5:** Commit `feat(memory): passages from the search index and the model pipe`.

### Task 2: Extraction prompt and parser

**Files:**
- Create: `src/archiv/memory/extract.py`
- Test: `tests/test_memory_extract.py`

**Interfaces:**
- Consumes: `Passage`.
- Produces:
  - `EXTRACT_PROMPT_VERSION = "1"`
  - `def ident(form: str) -> str` applies NFKC, casefold, and collapses whitespace to
    single spaces. It is the identity key used everywhere.
  - `@dataclass(frozen=True) class Mention: form: str; kind: str; segment_id: str`
  - `@dataclass(frozen=True) class Relation: source: str; phrase: str; target: str;
    segment_id: str`
  - `@dataclass(frozen=True) class Extraction: mentions: tuple[Mention, ...];
    relations: tuple[Relation, ...]; dropped: int`
  - `def batch_passages(passages: list[Passage], *, max_chars: int = 6000) ->
    list[list[Passage]]` groups consecutive passages, never splitting one passage. A
    single passage longer than `max_chars` is its own batch.
  - `def extraction_prompt(batch: list[Passage]) -> str` labels passages `[P1]…[Pn]`.
    It asks for every thing mentioned, of any kind, with
    `{"form", "kind", "passages": ["P1"]}`, and for every relationship the text
    states, with `{"source", "relation", "target", "passage"}`. It says to use the
    model's own words for kind and relation, to add nothing not in the text, and to
    output JSON only. The prompt names no example kinds.
  - `def first_json_object(text: str) -> dict[str, object] | None` returns the first
    balanced `{…}` that `json.loads` accepts, ignoring fences and any leading prose.
  - `def parse_extraction(answer: str, batch: list[Passage]) -> Extraction | None`
    returns `None` when there is no JSON. It maps `P#` back to `segment_id`. It drops,
    and counts in `dropped`, any mention whose passage ids are all unknown, any
    relation with an unknown passage, and any relation whose source or target is not
    among the batch's mention forms, compared by `ident`.

- [ ] **Step 1: Write failing tests.**
  - `test_prompt_labels_passages_and_names_no_kinds`: the prompt contains `[P1]` and
    `[P2]`, and contains none of `person`, `organization`, `location`, `place`.
  - `test_parse_handles_fenced_and_prose`: an answer with prose, then a fenced JSON
    block, returns 2 mentions and 1 relation, with `segment_id`s mapped.
  - `test_parse_drops_unknown_passage_and_unmentioned_target`: a relation citing `P9`,
    and one whose target is not a mention, are both dropped, so `dropped == 2`.
  - `test_parse_truncated_returns_none`: `'{"things": [{"form": "A"'` returns `None`.
  - `test_batches_never_split_and_respect_budget`: passages of 4000, 3000 and 9000
    characters with `max_chars=6000` give batches `[[4000], [3000], [9000]]`.
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): extraction prompt and tolerant parser`.

### Task 3: Store, cache and `archiv memory build` (extraction pass)

**Files:**
- Create: `src/archiv/memory/store.py`, `src/archiv/memory/build.py`,
  `src/archiv/memory/cli.py`
- Modify: `src/archiv/cli.py` (register `memory_app` under the name `memory`),
  `README.md` (add `archiv memory build` and `archiv memory ask` with a one-line
  description each)
- Test: `tests/test_memory_build.py`

**Interfaces:**
- Consumes: `load_passages`, `passages_by_object`, `Complete`, `batch_passages`, `ident`,
  `extraction_prompt`, `parse_extraction`, `EXTRACT_PROMPT_VERSION`.
- Produces:
  - `def memory_path(layout: ArchivLayout) -> Path` returns
    `layout.indexes / "memory.sqlite3"`.
  - `def open_memory(layout: ArchivLayout) -> sqlite3.Connection` creates these tables
    if absent:
    - `outputs(key PRIMARY KEY, text)`
    - `things(thing_id INTEGER PRIMARY KEY, ident UNIQUE, form)`, where `form` is the
      first form seen
    - `forms(thing_id, form, kind, segment_id, object_sha256)`
    - `relations(source_id, phrase, target_id, segment_id, object_sha256)`
    - `merges(a_id, b_id, same INTEGER, reason, segment_ids_json, PRIMARY KEY(a_id, b_id))`
  - `def cached(conn, key: str) -> str | None` and
    `def remember(conn, key: str, text: str) -> None`
  - `def cache_key(*parts: str) -> str` returns the sha256 of `"\x1f".join(parts)`.
  - `def store_extraction(conn, object_sha256: str, extraction: Extraction) -> None`
    first deletes that object's `forms` and `relations` rows.
  - `@dataclass class BuildSummary: documents: int; model_calls: int; cache_hits: int;
    failed_batches: int; things: int; relations: int; merges_confirmed: int = 0`
  - `def build_memory(*, home: Path | None, complete: Complete, model_label: str,
    max_chars: int = 6000) -> BuildSummary` runs extraction only. Task 4 adds
    `embedder: Embedder | None = None`, and Task 5 adds `max_merge_pairs: int = 500`.
  - CLI: `archiv memory build --model-cmd TEXT --model-label TEXT [--home]
    [--max-chars] [--json]`. `--model-cmd` defaults to `$ARCHIV_MODEL_CMD`, is split
    with `shlex`, and is required. The command prints the `BuildSummary`.

- [ ] **Step 1: Write failing tests.** Use a fake `complete` that counts calls and
  returns canned JSON keyed on the passage text.
  - `test_build_extracts_and_stores`: 2 documents give the expected `things` and
    `relations` counts, and every `forms.segment_id` exists in the search index.
  - `test_rerun_makes_zero_calls`: a second `build_memory` gives `model_calls == 0`
    and `cache_hits == first.model_calls`.
  - `test_label_change_recalls`: the same input with a new `model_label` gives
    `model_calls > 0`.
  - `test_failed_batch_does_not_stop_build`: the fake returns `"sorry"` for one batch,
    so `failed_batches == 1` and the other document is stored.
  - `test_unicode_identity`: forms `"Café Board"` (composed) and `"Café Board"`
    (decomposed, `e` + U+0301) map to one `thing_id`, and both forms are kept in
    `forms`.
  - `test_cli_build_json`: run through `CliRunner` with `--model-cmd` set to a
    `python -c` echo script. The command exits 0 and its JSON has `documents`.
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement. Iterate over objects in sorted order. Each batch's key is
  `cache_key(object_sha256, model_label, EXTRACT_PROMPT_VERSION, prompt)`.
- [ ] **Step 4:** Run the tests, `tests/test_documentation_coverage.py` and the full
  checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): build command with cached extraction`.

### Task 4: Meaning vectors

**Files:**
- Create: `src/archiv/memory/vectors.py`
- Modify: `src/archiv/memory/build.py` (embed passages after extraction),
  `src/archiv/memory/cli.py` (build `FastEmbedder` when the extra is installed;
  otherwise print a plain-words notice and skip vectors)
- Test: `tests/test_memory_vectors.py`

**Interfaces:**
- Produces:
  - `class Embedder(Protocol): name: str; def embed(self, texts: list[str]) -> NDArray[np.float32]: ...`
    returns L2-normalised rows.
  - `class FastEmbedder` defaults to
    `name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"`. It sorts
    inputs by length before embedding and restores the original order afterwards.
  - `class HashEmbedder` is a test-only, dependency-free fake that lives in
    `tests/memory_support.py`: a bag of hashed lowercase words projected into 64
    dimensions.
  - `@dataclass class VectorIndex: ids: list[str]; vectors: NDArray[np.float32]` with
    - `save(self, path: Path) -> None` (`.npy` plus `.ids.json`)
    - `@classmethod load(cls, path: Path) -> VectorIndex`
    - `nearest(self, query: NDArray[np.float32], k: int, floor: float, exclude: set[str] = set()) -> list[tuple[str, float]]`
  - Files: `indexes/memory-passages.npy` and `.ids.json`. Re-embedding is skipped when
    the stored ids equal the current passage ids and `embedder.name` matches, which is
    recorded in `.ids.json`.

- [ ] **Step 1: Write failing tests.**
  - `test_nearest_orders_and_floors`: three known vectors give ordered results, and
    results below `floor` are excluded.
  - `test_save_load_roundtrip`
  - `test_build_skips_reembed_when_unchanged`: a counting `HashEmbedder` is called
    once across two builds.
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement. Import `fastembed` lazily inside `FastEmbedder` only.
- [ ] **Step 4:** Run the tests and the full checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): passage meaning vectors`.

### Task 5: Merging the same thing (propose + confirm)

**Files:**
- Create: `src/archiv/memory/merge.py`
- Modify: `src/archiv/memory/build.py` (run merging after vectors, when an embedder
  is present)
- Test: `tests/test_memory_merge.py`

**Interfaces:**
- Consumes: `open_memory`, `Embedder`, `VectorIndex`, `cached`, `remember`,
  `cache_key`, `first_json_object`.
- Produces:
  - `MERGE_PROMPT_VERSION = "1"`
  - `def thing_texts(conn, *, context_passages: int = 2) -> dict[int, str]` gives each
    thing's distinct forms joined by `" / "`, plus up to 2 of its passages truncated
    to 300 characters.
  - `def propose_pairs(conn, embedder: Embedder, *, k: int = 3, floor: float = 0.80,
    max_pairs: int = 500) -> list[tuple[int, int, float]]` returns pairs with
    `a < b`, highest score first, skipping pairs already in `merges`.
  - `def merge_prompt(conn, a: int, b: int) -> tuple[str, list[str]]` returns the
    prompt and the cited `segment_id`s: each side's forms plus up to 3 passages. The
    prompt asks whether both sides refer to the same thing, as
    `{"same": true|false, "reason": "..."}`.
  - `def confirm_pairs(conn, pairs, complete: Complete, model_label: str) -> int`
    stores every verdict, returns the count of `same=1`, and stores nothing for an
    unparseable answer.
  - `def canonical_ids(conn) -> dict[int, int]` uses union-find over `same=1` rows
    and maps every `thing_id` to the smallest id in its group.

- [ ] **Step 1: Write failing tests.**
  - `test_propose_excludes_decided_pairs`
  - `test_confirm_records_verdict_and_reason`
  - `test_unparseable_confirm_stores_nothing`
  - `test_canonical_ids_transitive`: 1~2 and 2~3 give all three ids mapped to 1.
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement. The cache key is
  `cache_key(f"{a}:{b}", model_label, MERGE_PROMPT_VERSION, prompt)`.
- [ ] **Step 4:** Run the tests and the full checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): propose and confirm same-thing merges`.

### Task 6: Ask (entry → walk → exit) and `archiv memory ask`

**Files:**
- Create: `src/archiv/memory/ask.py`
- Modify: `src/archiv/memory/cli.py` (add `ask`)
- Test: `tests/test_memory_ask.py`

**Interfaces:**
- Consumes: `open_memory`, `ident` (from `extract.py`), `canonical_ids`, `VectorIndex`, `Embedder`,
  `load_passages`, `search_documents` (existing, FTS literal), `Complete`,
  `first_json_object`.
- Produces:
  - `ENTRY_PROMPT_VERSION = "1"`, `EXIT_PROMPT_VERSION = "1"`
  - `NOTHING_FOUND = "The collection has nothing that bears on this question."`
  - `def entry_prompt(question: str) -> str` asks for
    `{"forms": [...], "related": [...]}`: the ways the subject may be written, and
    things likely connected. `def parse_entry(answer: str) -> list[str]`.
  - `def walk(conn, *, forms: list[str], question_vec, passages: VectorIndex,
    by_id: dict[str, Passage], home: Path | None, hops: int = 2,
    max_passages: int = 60, floor: float = 0.55) -> list[Passage]`. Seeds are things
    whose `ident` equals an `ident(form)`, expanded through `canonical_ids`. It
    follows `relations` in both directions for `hops` levels and collects the
    passages of every visited mention and relation. It adds vector-nearest passages
    to `question_vec`, and FTS hits from `search_documents(form, limit=10)` for each
    form. Results are de-duplicated, keeping first-found order, and capped.
  - `def exit_prompt(question: str, passages: list[Passage]) -> str` labels passages
    `[P1]…`. It asks for a plain-language report organised by what the material shows
    matters, where every sentence ends with the `[P#]` labels supporting it, and where
    nothing is used that the passages don't say.
  - `@dataclass class Report: text: str; cited: list[Passage]; unknown_labels:
    list[str]; uncited_sentences: int`
  - `def check_report(answer: str, passages: list[Passage]) -> Report` resolves
    labels, lists labels that don't exist in `unknown_labels`, and counts sentences
    with no label.
  - `def ask(question: str, *, home: Path | None, complete: Complete, model_label: str,
    embedder: Embedder) -> Report` returns `Report(NOTHING_FOUND, [], [], 0)` when the
    walk is empty. In that case `complete` is not called for the exit.
  - CLI: `archiv memory ask QUESTION --model-cmd --model-label [--home] [--json]`
    prints the report, then a numbered source list
    (`source_name`, `locator`, first 160 characters). If `unknown_labels` or
    `uncited_sentences` is non-zero, it prints a plain-words warning line.

- [ ] **Step 1: Write failing tests**, with a fake `complete` and `HashEmbedder` over a
  3-document fixture where a relation chain links thing A → B → C.
  - `test_walk_follows_relations_two_hops`: forms `["A"]` reach C's passage.
  - `test_walk_uses_merges`: with a merge A~A2 confirmed, seeding `"A2"` reaches A's
    relations.
  - `test_nothing_found_makes_no_exit_call`: a question with no match returns
    `NOTHING_FOUND`, and the fake recorded only the entry call.
  - `test_check_report_flags_unknown_and_uncited`: an answer with `[P1]`, `[P7]` and
    one bare sentence gives `unknown_labels == ["P7"]` and `uncited_sentences == 1`.
  - `test_cli_ask_prints_sources`
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): ask command with entry, walk and cited exit`.

### Task 7: Blind probes for judging (`archiv memory probe`)

**Files:**
- Modify: `src/archiv/memory/cli.py`, `README.md` (add `archiv memory probe`)
- Test: `tests/test_memory_probe.py`

**Interfaces:**
- Produces:
  - `def draw_probes(conn, *, n: int, seed: int) -> list[str]` draws `n` things at
    random, uniformly over `thing_id`, using `random.Random(seed)`, and returns
    `f"What does the collection hold about {form}?"` for each.
  - CLI: `archiv memory probe --n 10 --seed N --model-cmd --model-label [--out DIR]`.
    For each probe it writes `DIR/probe-XX.md` with the question, the memory report and
    sources, and the FTS baseline (`search_documents(form, limit=20)`) listed beside
    it. It also writes `DIR/judge.md`, a table with one row per probe and blank
    columns *useful? / beyond search? / any claim wrong?* for the user to fill in.

- [ ] **Step 1: Write failing tests.** `test_probes_are_seeded_and_uniform` (the same
  seed gives the same list) and `test_probe_cli_writes_files`.
- [ ] **Step 2:** Run the tests. Expect FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and the full checks. Expect PASS.
- [ ] **Step 5:** Commit `feat(memory): seeded blind probes with search baseline`.

### Task 8: First real run (not code; outside the repo)

- [ ] **Step 1:** Write a wrapper script **outside the repo**:
  `~/archiv-eval/bin/haiku-low`. It runs `claude -p --model haiku --effort low
  --output-format json --system-prompt "<short>" --tools "" --strict-mcp-config
  --no-session-persistence`, appends `usage` and `total_cost_usd` to
  `~/archiv-eval/logs/haiku-usage.jsonl`, and prints only `.result`.
- [ ] **Step 2:** Copy the eval home and its search index into a scratch home. Restrict
  it to **30 randomly drawn documents** (seeded), and run `archiv memory build
  --model-cmd ~/archiv-eval/bin/haiku-low --model-label haiku-5.5-low`.
- [ ] **Step 3:** Report to the user: model calls, total tokens, cost per document and
  per MB from the usage log, things, relations and merges.
- [ ] **Step 4:** Run `archiv memory probe --n 10 --seed <random>`. The user fills in
  `judge.md`.
- [ ] **Step 5:** Tune prompts and thresholds only against that judgement, bumping the
  prompt versions. Repeat Steps 2–4 with a new seed.

### Task 9: Retire the regex reader (gated)

**Gate:** start only after the user's judgement in Task 8 says the memory reports beat
search.

**Files:**
- Modify: `src/archiv/graph/builder.py` (delete `_DISALLOWED_NAMES`,
  `_DISALLOWED_WORDS`, `_clean_person_name` and the `[A-Z][a-z]+ [A-Z][a-z]+` mention
  pass)
- Modify the tests that assert regex mentions (`tests/test_entity_graph.py`,
  `tests/test_entity_graph_precision.py`). Keep the face and date edges and their
  tests.

- [ ] **Step 1:** Change the regex-mention assertions to assert that no
  `candidate_mention` nodes come from text. Run them and expect FAIL.
- [ ] **Step 2:** Delete the regex pass and the blocklists.
- [ ] **Step 3:** Run the full checks. Expect PASS.
- [ ] **Step 4:** Commit `refactor(graph): remove regex person reader; text entities come from archiv memory`.
