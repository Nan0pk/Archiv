# Harness evidence baseline

## Retained finding

A clean-room benchmark separated three questions:

1. Can the local model produce the required tool call?
2. Can an agent harness deliver that result through its adapter and orchestration loop?
3. Can an integrated workbench build and expose useful operator surfaces?

The corrected raw Qwen3 4B control passed the exact file-writing task and external validator. Tested heavyweight general-purpose harnesses failed through orchestration overhead, oversized prompts, adapter timeouts, watchdog behaviour, or misleading completion semantics.

CoWork-OS remained the strongest integrated workbench candidate, but its tested local path failed before actuation and still emitted a completion-style status. AionUi remained the strongest Office-focused alternative.

## Architectural consequence

Archiv starts with a small replaceable executor, deterministic capabilities, and external validation. It does not merge the disposable benchmark workflows or adopt a harness as sole cognitive authority.

## Provenance

This summary is derived from the retained verdict in `Nan0pk/harness-loom`, pull request 8, commit `a601210e249fe61f31076b2ca699fed341cf515a`. The experimental test branches and workflows are evidence vehicles, not Archiv product code.
