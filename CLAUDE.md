# Working on Archiv

Archiv is a local-first, evidence-backed document core: originals are preserved
byte-for-byte under their content hash, everything else is a rebuildable derivative, and
no answer is reported unless an independent validator confirms it.

> **Models propose. Validators decide whether work succeeded.**
>
> **And whatever is reported, it is reported in plain words.**

This file is the entry point for any session — human or AI — picking up work on this
repository. Read it first, then follow the resume procedure below.

---

## Resume procedure

Work is a strictly ordered queue of small steps. **Progress is derived by running checks,
never by reading a status line.** To find out where things stand:

```bash
python scripts/plan_status.py
```

It prints the first incomplete step. Then:

1. Read `docs/plan/steps/S<NN>.md` — it states what to read, what to write, what must not
   change, and what evidence proves the step done.
2. Do **only that step**.
3. Run the checks (below). Fix anything red.
4. Open **one** pull request for that step.

Do not edit status by hand anywhere. Do not batch steps. If a step turns out to be too
large to finish in one session, split it in `docs/plan/queue.json` and say so in the PR.

**Full detail:** `docs/plan/README.md` · **The queue:** `docs/plan/QUEUE.md` ·
**Settled decisions:** `docs/plan/DECISIONS.md` · **Environment gotchas:**
`docs/plan/TRAPS.md`

---

## Setup

The system `python3` is **3.11**; this project needs **3.12+**. Use a venv:

```bash
uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
```

## Checks that must pass before every pull request

```bash
ruff format --check .
ruff check .
pyright --pythonpath .venv/bin/python     # the flag matters — see TRAPS.md
pytest -q
archiv doctor --json
```

These are the `Fast checks / quality` required gate (`.github/workflows/fast-checks.yml`).
Any change under `src/**` additionally triggers `office-validation`, `field-trial` and
`offline-alpha`, so a source change is never as small as it looks.

**Known-good baseline** on `d9a9b8d`: 384 passed, 2 failed (both environmental — see
`TRAPS.md`), 2 skipped; ruff clean; pyright clean; 82% coverage.

---

## Hard rules

These come from `CONTRIBUTING.md`, `docs/definition-of-done.md`,
`docs/execution-contract.md` and `docs/security/threat-model.md`. They are not
negotiable, and a step that cannot be done without breaking one is a step that needs
re-planning, not an exception.

**Evidence**

- A capability is done only when an independent validator says so.
  `docs/definition-of-done.md`: *"Installation, a green process exit, or a model
  statement is not task success."*
- **Never mark a plan, milestone, or roadmap status without the acceptance artefact in
  the same change.** This is the specific failure this queue exists to prevent: PRs
  #114–#121 marked `docs/capability-expansion-plan.md` "Status: Implemented … verified
  with comprehensive acceptance tests" while the measurements those milestones specified
  were never produced.
- Never show a user a number that was not measured. No hardcoded confidence values.

**Data**

- Fixtures are **generated at test time**, never committed as binaries. Use
  `scripts/generate_fixture_corpus.py` or `tests/format_matrix_support.py`.
- `docs/security/threat-model.md`: *"Fixtures containing third-party or user documents
  are forbidden."*
- Never commit private documents, personal data, credentials, model weights, archives, or
  run ledgers. See `docs/public-repository-policy.md`.

**Boundaries**

- The model interface is **loopback-only** and validated in
  `src/archiv/model_adapter.py:29-51`. Do not weaken those rules. The remote evaluation
  adapter (step S04) is a separate, explicitly-labelled adapter — not a relaxation of
  this one.
- **No processor may download a model at runtime, ever.** Weights are pinned installed
  artefacts with a recorded SHA-256, or the processor records `skipped`. The cascade in
  `src/archiv/ingestion/visual_ocr.py:579-624` is the pattern to copy.
- Every new dependency is an **optional extra**. `pip install archiv-core` must keep
  working with no vision stack and no weights.
- Originals are immutable. Validation happens before `_store_original`, so rejected
  material can never create or replace a canonical original.

**Language**

- Write in plain English. This covers everything a person reads: chat replies, pull
  request text, commit messages, and code comments.
- Never use jargon, acronyms, or internal shorthand in place of saying what actually
  happened. This is not a style preference. Jargon hides problems: a reader who cannot
  tell whether something worked cannot catch a mistake, and neither can the person who
  wrote it. This repository exists because work was reported as finished when it was
  not — see the milestone failure recorded under **Evidence** above. Unclear writing is
  how that happens.
- The only exceptions: the reader used the term first, or asked for it.
- Identifiers still belong in the text. Step numbers, file paths, command names and test
  names are how a reader finds the thing being discussed. Name the thing in ordinary
  words, then point at it. `S03` on its own is not a sentence.
- State plainly what failed, what was skipped, and what is uncertain. A passing check is
  not a result — say what the result was.

**Process**

- One step, one PR. Squash-merge. Subject: `type: description (#NNN)`.
- Fill in `.github/pull_request_template.md` honestly — including the "remaining gap"
  line when there is one.
- Failure states get tests, not just success paths.
- No step is merged on its author's own judgement. See the reviewer below.

---

## The distinguished reviewer

Every step is reviewed by a second agent before it merges, and that agent's verdict
gates the merge. The repository owner has delegated merging so that clear direction and
available resources are enough to finish work — they are not a queue the work waits in.
What replaces their sign-off is this role, not nothing.

**The role.** The distinguished reviewer is the standing reviewer for this repository. It
is not the author's assistant and its job is not to agree. Its value is catching what the
author missed, so a review that finds nothing should be rare enough to be suspicious.

**How it reads a change — two passes, in this order.**

1. *The wide view, first, before looking at the diff at all.* What is this project trying
   to be, and what does it forbid? That means this file, `docs/plan/DECISIONS.md`,
   `docs/plan/TRAPS.md`, `docs/definition-of-done.md`, `docs/security/threat-model.md`,
   `docs/architecture.md`, and the step's own specification under `docs/plan/steps/`.
   Then, before reading a line of the change: write down what would make a change like
   this **wrong for this project even if the code were flawless**.
2. *Then zoom in, stepwise.* Read every changed file in full, not only the changed lines.
   Then widen again: read what calls the changed code, and check that it composes with
   the steps already merged rather than only working alone.

**It is a standing reviewer, not a fresh one per pull request.** The wide view is loaded
once and kept. Later reviews get the diff and a note of what changed, not the whole
project again — re-reading everything each time is waste. The author owes it one thing in
exchange: when a merged change alters the rules, decisions or traps above, say so
explicitly in the next review request. A reviewer working from a stale model of the
project is worse than one working from none, because it is confident.

**What it must do.**

- Run the checks itself rather than trusting the author's report of them, and report the
  actual output. Three failures are environmental and documented in `TRAPS.md` and
  `known-issues.md`; a fourth is real.
- Check the pull request text against the code. Overclaiming is the specific failure this
  queue exists to prevent, so a description that says more than the code does is itself a
  finding, not a wording nit.
- Try to break the change, and say plainly where it is uncertain rather than picking a
  side to sound decisive.
- End with one verdict line: `MERGE`, or `FIX FIRST` with numbered, specific problems,
  each naming a file and what would fix it.

**What it cannot do.** It cannot approve a change that fails a required check. It cannot
waive any hard rule in this file — a step that needs one waived needs re-planning. It
cannot mark a step complete; only the acceptance checks do that. And it cannot substitute
for the owner on a question that is genuinely theirs: anything ambiguous or
architecturally significant goes to them, and the reviewer saying so is a valid verdict.

**What it is not.** It is another instance of the same kind of model as the author, so it
shares blind spots and is not independent oversight in the strong sense. The independent
checks remain the test suite, the acceptance criteria, and continuous integration. The
reviewer is there to catch what one pass by one author misses — which, in practice, it
does.

**Order of work.** Review first, then push once, then merge on green. Reviewing after
pushing spends a continuous-integration run on code that is about to change, and a
verdict given on code that then changes is worth nothing. If continuous integration
finds something the reviewer could not — it runs elsewhere, on a clean machine, with jobs
that cannot run locally — the fix goes back through the reviewer before merging.

---

## Orientation

Where things live, for a session that has never seen this repo:

| Concern | Mechanism | File |
|---|---|---|
| Identity | SHA-256 content address; original stored read-only `0o444` | `storage/layout.py`, `ingestion/service.py:50-65` |
| Durability contract | Per-path classification and removal semantics | `docs/architecture.md` |
| Schema migrations | Numbered, idempotent, one per `BEGIN IMMEDIATE` | `storage/database.py:71-151` |
| Evidence | `NormalizedDocument` → `segments[{locator, text}]` | `contracts.py:88-113` |
| Search | SQLite FTS5, literal only, rebuildable | `search/schema.py`, `search/index.py` |
| Retrieval | Deterministic query expansion — no model, no vectors | `search/retrieval.py` |
| Trust | Citation revalidated against original **and** normalized hashes | `search/service.py` |
| Grounded answers | `ask` → `run_grounded_ask` → `runs/ask/<id>/` | `grounding.py:134-287` |
| Reports | `report` → `run_task` → DOCX + manifest + validation | `tasks.py`, `reports/generator.py` |
| Engine attribution | `locator["origin"]` rides with the text to every surface | `ingestion/visual_ocr.py:320-339` |
| Optional-feature gate | Opt-in file + raising check with remediation text | `faces/config.py:51-59` |

Two traps worth knowing before you touch either path:

- **`ask` and `report` do not share the model call.** `tasks.py:127-152` re-implements it
  and does **not** go through `run_grounded_ask`. Anything that must appear in both has to
  be threaded twice.
- **`format_locator` renders every unrecognised locator key** (`reports/formatting.py:37-38`).
  Adding a key to a segment locator therefore propagates to `find` output, `ask` output,
  the DOCX source table and the appendix with no further edits. Use this deliberately.
