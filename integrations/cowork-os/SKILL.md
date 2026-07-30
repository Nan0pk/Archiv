---
name: archiv-evidence-workflow
description: Use Archiv MCP for local evidence-backed retrieval and cited Office output without bypassing validators.
---

# Archiv evidence workflow

Use the Archiv MCP server as the source-of-truth boundary. CoWork-OS is the operator workbench, not the archive, executor of record, or final validator.

## Required operating sequence

1. Use `archiv_ingest` only for an explicit absolute local file supplied by the operator.
2. Use `archiv_search` for literal evidence retrieval.
3. Use `archiv_read_source` before relying on a retrieved excerpt in analysis.
4. Use `archiv_generate_docx` for cited report output.
5. Use `archiv_verify_artifact` after generation or any suspected artifact change.
6. Use `archiv_get_run_evidence` before reporting that a write or verification succeeded.

A host may display these names with an MCP prefix. Match by the underlying tool name rather than guessing a new capability.

## Completion rule

Treat a task as complete only when the returned structured envelope has:

- `status: succeeded`;
- the expected artifact or evidence fields;
- a run ID whose `archiv_get_run_evidence` result also records `status: succeeded`;
- for reports, `validation.valid: true`.

Never infer success from assistant prose, process exit alone, a partially created file, or the absence of an exception. An MCP result with `isError: true`, missing structured content, failed validation, or missing evidence is failure.

## Forbidden shortcuts

Do not use shell tools, generic filesystem tools, browser downloads, or CoWork document generators to imitate an Archiv operation. Do not edit canonical originals, SQLite ledgers, normalized evidence, report manifests, or MCP run records. Do not write reports outside Archiv's fixed MCP output directory through this workflow.

## Fault ownership

When work fails, report the narrowest owning component:

- **Archiv**: ingestion, search, citation, report, validator, or run-ledger evidence is wrong.
- **MCP transport**: initialize, tool discovery, framing, timeout, or `isError` propagation is wrong.
- **CoWork integration**: CoWork cannot spawn the configured stdio command, pass environment/cwd, retain tools, or return the raw MCP result.
- **Model provider**: the selected model cannot plan or choose tools despite a healthy connection.
- **Workbench orchestration**: CoWork's task loop reports completion without the required tool sequence/evidence.

Do not classify a model or orchestration failure as an Archiv defect merely because the workbench did not call the correct tool.

## Minimal report request

Ask the workbench to:

> Search Archiv for the exact phrase, read the cited sources, generate a cited DOCX, verify it, retrieve the generation and verification run evidence, and report only the validated artifact path and run IDs.

This wording preserves the bounded sequence without granting shell or arbitrary-file authority.
