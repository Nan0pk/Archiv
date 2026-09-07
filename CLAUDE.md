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

**Known-good baseline** measured on `1edeb92`, the head of
[#139](https://github.com/Nan0pk/Archiv/pull/139), run as
`PATH="$PWD/.venv/bin:$PATH" pytest -q`: **491 passed, 0 failed**, 2 skipped; ruff clean;
pyright clean. There is no expected failure any more — the one that was called
environmental was a real leak this file's own test was catching, and it is fixed. Keep
this current, and treat any failure as real: a tolerated red test is how the next one gets
ignored. Name the commit when you update it. The baseline this replaced named `ec96869`,
which is not reachable in this repository, so nobody could tell whether it had gone
stale.

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
  actual output. Run the suite as `PATH="$PWD/.venv/bin:$PATH" pytest -q`.
  There are no expected failures. Investigate every failure; report optional-dependency
  skips separately and name what was unavailable. See `TRAPS.md` for setup problems.
- Check the pull request text against the code. Overclaiming is the specific failure this
  queue exists to prevent, so a description that says more than the code does is itself a
  finding, not a wording nit.
- Try to break the change, and say plainly where it is uncertain rather than picking a
  side to sound decisive.
- End with one verdict line: `MERGE`, or `FIX FIRST` with numbered, specific problems,
  each naming a file and what would fix it.

**How the author briefs it.** Say what the change was trying to do, briefly, and stop
there.

- **Do not list things to look at.** A checklist tells the reviewer where the author has
  already looked, which is precisely where the defects are not. Steering it toward the
  parts the author finds interesting steers it away from the parts they have not thought
  about, and those are the ones that ship.
- Do not ask it to confirm a property, argue for the change, or explain why a decision
  was right. It will read the code. An argument in the brief only tells it which
  conclusion the author wants, and its whole value is not wanting one.
- Do say plainly what is unverified — a job that could not be run, a platform not
  available — because that is a fact about the evidence, not a hint about where to look.
- Facts it cannot get from the diff belong in the brief: the current commit, what changed
  in the rules since last time, which steps the batch covers. Check those before stating
  them; a wrong commit hash in a brief is the same carelessness as a wrong claim in a
  pull request.

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

**Work in batches, and know when to stop.** The reviewer exists to catch defects, not
to polish. Getting something substantial finished beats getting one thing perfect:

- Take related steps together and review them as one batch. One step per review round
  spends most of the effort on the round trip rather than on the work.
- A round ends when what it found is fixed and verified. Fix, check by running, land.
  Going back for confirmation that the fix was fixed is a fifth round on a third-order
  detail, and the queue does not move while it happens.
- Something the reviewer finds that is not a defect in shipped behaviour — a gap worth
  closing later, a limitation worth knowing, a claim worth narrowing — becomes a queue
  step or an entry in `docs/known-issues.md`. It does not become another round, and it
  does not widen the change that found it.
- The exception, and the only one: a defect that is live for users, or a false statement
  in something a user reads, is fixed before the change lands however many rounds that
  takes. Correctness of what we tell people is not the thing to trade away for speed.

Perfect is the enemy of excellent here. A change that is excellent and merged has moved
the project; a change that is perfect and still open has not.

**Order of work.** Review first, then push once, then merge on green. Reviewing after
pushing spends a continuous-integration run on code that is about to change, and a
verdict given on code that then changes is worth nothing. If continuous integration
finds something the reviewer could not — it runs elsewhere, on a clean machine, with jobs
that cannot run locally — the fix goes back through the reviewer before merging.

**What merging requires, exactly.** Three things, and green checks are only one of them:

1. A reviewer verdict of `MERGE`, stated. Not inferred from silence, and not from a round
   whose findings are still open.
2. Green checks **on the commit being merged**. Verify that by reading the workflow run's
   own `head_sha`, not the list of check runs on the pull request — that list has more
   than once shown results belonging to a previous head. Pass the expected head to the
   merge itself so the merge fails rather than succeeding on the wrong commit.
3. No merge conflict.

Green checks alone are never enough. A change merged on checks alone has been reviewed by
nobody: continuous integration proves the code runs, not that it does what its description
says, and overclaiming is the specific failure this queue exists to prevent. That has
happened here — a step merged with review findings still outstanding, putting a defect on
`main`. If auto-merge is enabled on the repository, do not arm it; it merges on checks and
therefore skips requirement 1.

**Report a check only after reading its output.** Never pipe a check into `tail` or `head`
inside an `&&` chain: the pipeline takes the exit status of `tail`, which always succeeds,
so a failing check passes silently and the chain continues. That has happened here too —
type checking failed, the failure was invisible, and "type checking clean" went into a
pull request description and a report to the owner. Run each check on its own, read its
exit code, and quote what it actually said.

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
