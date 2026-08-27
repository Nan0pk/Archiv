# Archiv ingestion and supply-chain threat model

**Status:** alpha security baseline. **Assets:** canonical originals, durable SQLite state,
derived evidence, reports, local-model weights, credentials, and the user's workstation.
Inputs, document text, archive metadata, templates, model output, installers, and update
artifacts are untrusted. Archiv is local-first, not a sandbox or malware scanner.

## Trust boundaries and required controls

| Threat | Abuse case | Required control and residual risk |
|---|---|---|
| Malicious documents | A parser exploit, active content, external relationship, or malformed object gains execution or leaks data. | Parse without macros, links, queries, or network access; validate before preserving; keep parsers patched. Native parser defects remain possible. |
| Archive bombs | ZIP/OOXML/ODF members consume disk or memory through count, size, ratio, nesting, duplicates, or overlapping names. | Inspect the central directory before parsing; reject unsafe names, links, excessive depth, entries, member size, total expansion, and ratio; never extract to user paths. |
| Parser exhaustion | Huge pages, pixels, XML trees, rows, cells, or crafted compression consumes CPU/RAM. | Enforce the limits below before expensive work, bound format-specific structures, and stop subprocesses on timeout/resource limits. In-process Python parsers still share the application process. |
| Symlink/path attacks | A source or container member escapes its intended root or races a path check. | Reject source symlinks and non-regular files; reject absolute, parent, backslash, duplicate, and symlink archive members; content-address copies and verify hashes before and after processing. A privileged hostile local user remains out of scope. |
| Local-model compromise | Tampered weights or a model response causes execution, exfiltration, or false claims. | Models propose data only; deterministic validators decide; do not give models tools, network, templates, or filesystem authority; pin and verify model artifacts. Model output remains untrusted content. |
| Report-template injection | Document/model text becomes template syntax, HTML, fields, links, or formulas. | Treat values as data, use fixed bundled templates and context-aware escaping, disallow active links/macros, and validate rendered reports. Visual deception remains possible. |
| Unsafe desktop opening | Preview/open actions invoke a shell, handler, or active office content. | Never shell-interpolate paths; require explicit user action; open only generated inert previews/reports with argument arrays; warn before handing originals to another application. Desktop handlers are outside Archiv's boundary. |
| Installer compromise | A release, dependency, CI runner, or signing identity supplies altered code. | Protected release workflow, pinned actions/dependencies, signed tags/artifacts, checksums, SLSA provenance, least-privilege CI, and independent verification. Account or runner compromise remains possible. |
| Durable-state tampering | SQLite rows, originals, derived files, or ledgers are replaced, rolled back, or partially written. | Read-only content-addressed originals, atomic writes, schema constraints, recorded hashes, integrity checks, and rebuildable derived data. Local rollback requires an external trusted backup/checkpoint to detect. |

## Enforced resource budget

`src/archiv/ingestion/limits.py` is the common fail-closed policy. Current ceilings are:
256 MiB input; 4,096 archive entries; 32 MiB/member; 128 MiB total expansion;
200:1 expansion; 250 pages; 80 million pixels; eight path components; one subprocess;
60 CPU seconds; 1 GiB address space; and 60 seconds wall time. Format-specific limits may
be lower. An exceeded boundary raises a named error and the CLI must show it. Validation
happens before `_store_original`, so rejected material cannot create or replace a canonical
original. Derivation never writes to the original and post-processing hashes detect change.

## Security verification

Adversarial tests use only generated synthetic bytes and cover malformed ZIP/OOXML/ODF,
PDF, image, SQLite/ODB, backup-style nested archives, citation-envelope JSON, and InPage
inputs. Fixtures containing third-party or user documents are forbidden. A regression is
retained only as the smallest lawful generator or byte fixture, with its expected rejection.

Security-limit failures, parser crashes, hangs, memory spikes, changes to originals, or
unexpected external I/O fail release qualification. Run dependency review and static
analysis on every change and fuzz parsers before each stable release.

## Alpha-to-stable gate

The product **must remain alpha** until an independent security firm or qualified reviewer
has assessed ingestion, report generation, desktop launching, durable state, installer,
release signing, and provenance. Track findings privately, fix all critical/high findings,
record accepted lower-risk findings with owners and dates, and publish a non-sensitive
review scope and remediation summary. This document records the requirement; it does not
claim that the review has occurred.
