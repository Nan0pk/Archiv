# Settled decisions

Decisions already taken for this plan, with the reasoning that produced them. Read this
before proposing an alternative. Changing one of these is allowed, but it is a decision
in its own right — record it here with a date and a reason rather than quietly diverging.

---

## 1. A remote model is permitted, but only as a labelled exception

**Decided.** Archiv gains a third model adapter that it *knows* is remote. Every output
derived from it is stamped as non-local, and it refuses to run against any archive not
explicitly marked for evaluation.

**Why.** The project's README states that remote hosts are *"rejected. Not discouraged —
rejected."* That claim is load-bearing, and the author currently has no GPU, so `ask` and
`report` cannot be exercised at all. The resolution is an exception Archiv declares and
labels, not a silent relaxation.

**The rejected alternative, and why.** A small proxy on `127.0.0.1` forwarding to a cloud
provider would work in an afternoon and needs no code change — Archiv would see only
loopback and validate happily. That is precisely the problem: every answer would claim
local provenance while documents left the machine. Rejected.

**Consequences.**

- `ModelConfig` gains a `provenance` field derived from the adapter, never set by hand.
- A remote run against an unmarked archive returns `blocked_by_policy`.
- The egress-denied acceptance run asserts the remote adapter stays refused, so the
  offline guarantee remains a tested claim rather than a promise.

---

## 2. The seam is the `ModelAdapter` protocol, not the wire format

**Decided.** The stand-in implements `ModelAdapter.complete(prompt) -> str`
(`model_adapter.py:54-57`). `OpenAICompatibleLoopbackAdapter` and its URL validation are
not modified.

**Why.** That single method is the whole contract the rest of Archiv depends on. Treating
the OpenAI wire format as the seam would mean widening the loopback validator to admit a
remote host — the one change that genuinely damages the privacy boundary.

---

## 3. The remote adapter is provider-agnostic over the OpenAI-compatible wire format

**Decided.** The remote adapter speaks OpenAI-compatible `/v1/chat/completions` with the
provider, base URL, and model set by configuration. It reuses the existing standard-library
client shape rather than adding a vendor SDK.

**Why.**

- Nearly every provider exposes an OpenAI-compatible endpoint, so switching provider is
  configuration rather than code.
- The eventual local target — Ollama on `127.0.0.1:11434`, already a known preset at
  `ui/product.py:23` — speaks the same wire format. Using it for both maximises the
  drop-in property: when hardware arrives, only the endpoint and the policy change.
- No new default dependency. `pip install archiv-core` keeps working unchanged.

**Open.** The specific default provider and model. The author has indicated a provider
other than Anthropic but has not yet named it; step S04 records the choice when made.
Nothing else in the plan is blocked by it.

---

## 4. Time is predicted; quality is measured

**Decided.** Calibration predicts local *wall-clock* by extrapolation, and reports local
*quality* only from direct measurement. Predicted figures are labelled `estimated` and
carry a range; measured figures are labelled `measured`.

**Why.** Archiv's evidence packages are bounded — `evidence_limit` defaults to 8 and is
capped at 50 — so prompt and completion token counts per question are stable and
measurable. That makes a linear latency model defensible:

```
predicted_ask_ms ≈ retrieval_ms                       (local, model-independent)
                 + prompt_tokens     / prefill_tokens_per_second
                 + completion_tokens / decode_tokens_per_second
```

`retrieval_ms` is already recorded per question by the field-trial runner, and token
counts are exact. Only two throughput constants are unknown, and they are properties of
the hardware.

Quality is different in kind. General benchmark scores do not predict citation fidelity
on a specific corpus, and inventing a scaling factor for it would be the same category of
error this whole plan exists to correct. Instead the existing frozen 22-question fixture
is run against both models and the difference is reported as measured.

---

## 5. Faces: a real detector, and names only from checkable evidence

**Decided.** The skin-tone connected-component heuristic is replaced by a real detector on
pinned weights. Name candidates come only from sources a user can open — EXIF `Artist` /
`XPAuthor`, IPTC byline, XMP `dc:creator`, or a document segment that names the image —
each shipped with a resolvable citation. Filename-derived names are removed entirely.

**Why.** The current detector reports a vase as `Person 1` with the candidate name
"Terracotta Vase (52% conf)" taken from the filename, and its confidence value is
`0.50 + fill_ratio × 0.45` — an arithmetic restatement of blob solidity, not a detection
probability. Author's explicit choice among the options offered.

**Consequence.** Until the real detector lands, faces ships as clustering-only with no
name hypotheses — which is the fallback
[`docs/capability-expansion-plan.md`](../capability-expansion-plan.md) already specified.

---

## 6. Two of the three September capabilities are kept, under honest names

**Decided.** Perceptual near-duplicate detection and citation-backed co-occurrence are
genuinely useful and stay. What goes is the text-query surface on image search
(a nineteen-entry colour lookup with a hash-scatter fallback for every other word), the
filename name guessing, and every confidence number that was never measured.

**Why.** The capabilities are not the problem; the labels and the score columns are. A
ranked table with four-decimal scores implies a calibrated similarity that does not exist.

---

## 7. Plan progress is derived, never declared

**Decided.** `scripts/plan_status.py` determines the next step by running each step's
acceptance check. There is no status field to edit.

**Why.** See [`README.md`](README.md) — this is the direct countermeasure to the failure
that produced the assessment.

---

## 8. Plan lives in the repository, publicly

**Decided.** `CLAUDE.md` at the repository root, detail under `docs/plan/`. Author's
explicit choice.

**Why.** `CLAUDE.md` is the one file a fresh Claude Code session reads automatically,
which is what makes resuming reliable rather than dependent on someone remembering to
paste context. Keeping it out of git would defeat the purpose, since it would not survive
a fresh clone.

---

## 9. Media processing gets its own declared resource budget

**Decided.** Audio and video do not raise the global ceilings in
`src/archiv/ingestion/limits.py`. They declare a separate, explicitly documented media
budget.

**Why.** The current policy allows one subprocess, 60 CPU seconds, 1 GiB of address space
and 60 seconds of wall time — appropriate for a document, far too small for an hour of
video, and raising it globally would weaken the fail-closed guarantee for every document
format at once. Video is also the one input where a single file can produce thousands of
derived objects, so its ceilings need to be specific and its overflow behaviour needs to
be a clean `degraded` outcome that keeps the original and the transcript.
