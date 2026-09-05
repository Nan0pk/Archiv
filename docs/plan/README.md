# The work queue

> **How to find out where things stand:** `python scripts/plan_status.py`

This directory holds a strictly ordered queue of small work steps, designed so that any
session — including one with no memory of what came before — can determine the current
state by running a command, do exactly one step, and stop.

## Why it is built this way

The problem this guards against is recorded, not hypothetical. Pull requests #114–#121
delivered nine milestones and then marked
[`docs/capability-expansion-plan.md`](../capability-expansion-plan.md)
**"Status: Implemented … verified with comprehensive acceptance tests"** while the
measurements those milestones themselves specified — recall@k on lawful fixtures,
detection metrics, precision figures — were never produced. Three shipped capabilities
turned out to be heuristic placeholders behind confident-looking output.

A plan whose progress is written down as prose can fail in exactly that way again. So:

**Progress here is derived by running checks, never by reading a status line.**

Each step declares an acceptance check in [`queue.json`](queue.json). `plan_status.py`
runs those checks and reports the first step that does not pass. A forgotten checkbox is
therefore harmless, and a regression in finished work is immediately visible — if an
earlier step breaks, the reported next step moves *backwards* to it.

This mirrors a discipline the project already relies on. Database migrations
(`src/archiv/storage/database.py:71-151`) are numbered, idempotent, run one per
`BEGIN IMMEDIATE` transaction, and advance their version marker last, so a crash leaves
either the old state or a complete new one. The step queue is that same pattern applied
to project work rather than to schema.

## Files

| File | What it is |
|---|---|
| [`queue.json`](queue.json) | **The single source of truth.** Ordered steps, dependencies, and each step's acceptance check. |
| [`QUEUE.md`](QUEUE.md) | Human-readable view of the same data. A test asserts the two agree, so they cannot drift. |
| [`steps/`](steps/) | One file per step, carrying the project's own capability-declaration block. |
| [`DECISIONS.md`](DECISIONS.md) | Decisions already settled. Read before proposing an alternative; do not re-litigate. |
| [`TRAPS.md`](TRAPS.md) | Environment gotchas that have already cost real time. |

## The procedure

1. **Find the next step.**

   ```bash
   python scripts/plan_status.py
   ```

   It prints every step's state, then names the next incomplete one and lists exactly
   which acceptance targets are not yet passing.

2. **Read that step's file** under [`steps/`](steps/). It states what the step reads,
   writes, must not change, what network access it may use, what evidence it emits, and
   which validator decides success — the block defined in
   [`docs/execution-contract.md`](../execution-contract.md).

3. **If the step is marked a stub, expand it first.** Steps in phases B–E carry intent
   but not full detail, because detail written months ahead of implementation is usually
   wrong. Expanding a stub into a full step declaration is itself a legitimate, small
   piece of work — do that, commit it, then implement.

4. **Do only that step.** Resist adjacent tidying; it makes the PR unreviewable and
   breaks the one-step-one-PR property that lets a lost session resume cleanly.

5. **Run the checks.**

   ```bash
   ruff format --check . && ruff check . \
     && pyright --pythonpath .venv/bin/python \
     && pytest -q && archiv doctor --json
   ```

6. **Open one pull request**, filling in
   [`.github/pull_request_template.md`](../../.github/pull_request_template.md) honestly.

7. **Do not hand-edit status anywhere.** There is no status field to edit. If a step's
   acceptance check is wrong, fix the check in `queue.json` and say so in the PR.

## Sizing rule

**Every step must be completable in one session.** This is the real constraint, not an
estimate in days — a step too large to finish is a step a dying session leaves half-done.
If a step turns out to be bigger than that, split it in `queue.json`, note the split in
the PR, and carry on. Splitting is expected and is not a failure.

## Ordering

The order is fixed and deliberate:

- **Phase A** comes first because the product is currently untestable by its own author —
  there is no local GPU, so `ask` and `report` cannot be exercised at all. Within Phase A
  the loopback privacy boundary is pinned by tests (S01) *before* the remote adapter
  exists (S04), because that boundary is the riskiest thing in the plan and it is being
  touched first.
- **Phase B** makes every user-facing number honest. It is deliberately cheap and comes
  before the deep fixes so that nothing overclaims while those take their time.
- **Phase C** recovers content that is being silently dropped today — measured: a slide
  with a title, a table and a speaker note yields one segment.
- **Phases D and E** add audio, video, real face detection, and Arabic-script identity.

Re-ordering within a phase is fine when dependencies allow it; `depends_on` in
`queue.json` is authoritative. Moving work between phases should be a recorded decision
in [`DECISIONS.md`](DECISIONS.md).
