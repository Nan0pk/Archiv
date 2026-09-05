# Working on Archiv

Archiv is a local-first, evidence-backed document core: originals are preserved
byte-for-byte under their content hash, everything else is a rebuildable derivative, and
no answer is reported unless an independent validator confirms it.

> **Models propose. Validators decide whether work succeeded.**

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

**Process**

- One step, one PR. Squash-merge. Subject: `type: description (#NNN)`.
- Fill in `.github/pull_request_template.md` honestly — including the "remaining gap"
  line when there is one.
- Failure states get tests, not just success paths.

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
