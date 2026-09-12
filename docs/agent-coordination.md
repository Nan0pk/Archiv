# Multi-agent coordination

Archiv may be worked on concurrently by humans, ChatGPT, Codex, Claude, local CLI sessions, other AI systems, bots, and automation. This protocol lets those workers avoid collisions and hand work to one another through GitHub.

This is a coordination layer only. It does **not** replace the ordered plan in `docs/plan/queue.json`, change priority, prove a step complete, waive acceptance evidence, or replace the distinguished reviewer in `CLAUDE.md`.

## Shared rule

Assume another worker may be active unless fresh repository evidence says otherwise. Before starting or resuming write work, inspect:

- the first incomplete plan step and its specification;
- open issues whose title starts with `[agent-claim]`;
- open pull requests and their current head/base;
- recent branches and commits relevant to the same files or subsystem;
- review comments and CI on overlapping work.

Absence of a ChatGPT task or ChatGPT-authored pull request is not evidence that work is free.

## Work claims

A worker that is about to make a substantial repository change opens one coordination issue using the **Agent work claim** template. The claim must identify:

- the canonical plan step or other owner-authorized work;
- the worker/session identity in a form another agent can distinguish;
- the role (`builder`, `reviewer`, `coordinator`, or `verifier`);
- the bounded scope and likely files/subsystem;
- branch or pull request when known;
- current state;
- a UTC lease expiry when the worker expects to remain active.

A claim is temporary ownership of a work surface, not authority over project direction.

### Lease behavior

Refresh the claim while actively working. When stopping, change it to a truthful terminal or handoff state and record the exact next action.

An expired lease is a warning, **not permission to overwrite work**. Before taking over an expired claim, inspect its branch, pull request, commits, comments, CI, and recent activity. Take over only when the work is clearly abandoned or explicitly handed off, and record the takeover in the issue.

## Collision rule

If another worker appears active on the same step, branch, pull request, files, decision, or tightly coupled subsystem:

1. do not start a competing implementation;
2. do not push to that worker's branch unless it is explicitly shared and coordination is clear;
3. choose the highest-priority non-overlapping work that Archiv's ordered queue and repository rules permit;
4. otherwise hold the conflicting work until it is integrated, abandoned, or handed off.

When ownership is ambiguous, behave conservatively rather than assuming the work is yours.

## Builder handoff to review

When a coherent pull-request head is ready for independent review, the builder posts a top-level PR comment in this form:

```text
Agent handoff v1
role: builder
worker: <worker/session>
work: <canonical step or authorized work>
claim: <issue number or none>
head: <full SHA>
base: <full SHA>
state: review-ready
unverified: <facts the builder could not verify, or none>
```

The brief states facts only. It must not steer the distinguished reviewer toward a desired conclusion.

## Review claim and verdict

Before reviewing, a reviewer checks whether another reviewer is already actively reviewing the same unchanged head. If not, it records:

```text
Agent review claim v1
role: reviewer
worker: <worker/session>
head: <full SHA>
base: <full SHA>
state: reviewing
```

The review then follows the distinguished-reviewer rules in `CLAUDE.md`. The final review record must be tied to the exact reviewed head/base and end with the repository-required verdict: `MERGE` or `FIX FIRST`.

A reviewer must not silently fix the implementation it is reviewing. If it changes implementation or test behavior, it becomes a builder for that new head and another independent reviewer is required.

## SHA invalidation

A review authorizes only the exact head/base it reviewed. If the builder pushes a changed head, or the base changes in a way that changes integration, the old verdict is stale. The changed state must be reviewed again before merge.

CI never substitutes for independent review. Independent review never substitutes for required CI or acceptance evidence.

## Handoffs and stopping

Before a worker stops substantial work, repository-visible state must say:

- what was completed;
- branch and pull request, if any;
- exact current head when relevant;
- actual review/CI state;
- blocker, if any;
- exact next action;
- whether the claim is still active, handed off, blocked, or finished.

Do not leave essential continuation context only in a private chat session.

## Machine-readable vocabulary

Use these coordination states consistently where practical:

- `working`
- `review-ready`
- `reviewing`
- `fix-required`
- `merge-ready`
- `blocked`
- `handed-off`
- `finished`

These words describe coordination only. They do not alter Archiv's plan or completion state.

## Protocol version

Structured handoff blocks in this document use `v1`. Future changes must remain readable by older agents or clearly state how to interpret the new version.