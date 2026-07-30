# Product charter

## Problem

Serious knowledge work is split between powerful online AI services that require surrendering data and control, and fragmented local tools that are difficult to operate, verify, and combine.

## Purpose

Archiv will make private local files useful to controlled AI-assisted workflows while preserving source integrity, provenance, explicit permissions, and independently verifiable outputs.

## Product promise

A user should be able to preserve documents, find evidence across formats, request analysis, and produce usable Office artifacts without making an agent framework or vector index the owner of the underlying knowledge.

## Principles

- Local-first, with external services opt-in rather than assumed.
- Originals are immutable evidence.
- Derived indexes and embeddings are rebuildable accelerators.
- Deterministic capabilities perform bounded work.
- Models propose interpretations and tool calls.
- Validators decide whether required work succeeded.
- Interfaces and model providers remain replaceable.
- Simple direct execution precedes planning and multi-agent orchestration.

## First verified vertical slice

1. Read a source file containing a unique marker.
2. Produce an exact required output artifact.
3. Preserve the source hash.
4. Reopen and validate the output externally.
5. Record the request, effects, evidence, and final status.
6. Expose the operation through the CLI and later through MCP.

## Non-goals for the first milestone

- building an Office editor;
- building a desktop workbench;
- autonomous long-horizon agents;
- multi-user collaboration;
- cloud synchronization;
- a knowledge graph;
- a separate vector service;
- broad connector or messaging support.
