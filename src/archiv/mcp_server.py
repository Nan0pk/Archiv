"""Local stdio MCP server exposing only bounded Archiv capabilities."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from archiv.contracts import Citation
from archiv.mcp_contracts import McpRunEnvelope
from archiv.mcp_tools import (
    archiv_generate_docx as run_generate_docx,
    archiv_get_run_evidence as run_get_run_evidence,
    archiv_ingest as run_ingest,
    archiv_read_source as run_read_source,
    archiv_search as run_search,
    archiv_verify_artifact as run_verify_artifact,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
BOUNDED_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

mcp = MCPServer(
    "Archiv",
    version="0.0.1",
    instructions=(
        "Archiv is local-only. Use its task-specific tools for immutable ingestion, validated "
        "retrieval, exact source reading, cited DOCX generation, artifact verification, and "
        "bounded run evidence. It exposes no shell, URL fetcher, arbitrary output path, or network."
    ),
)


@mcp.tool(
    name="archiv_ingest",
    title="Ingest a local file into Archiv",
    annotations=BOUNDED_WRITE,
)
def archiv_ingest(
    source_path: Annotated[
        str,
        Field(description="Absolute path to one local non-symlink regular file."),
    ],
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Validate and preserve one local file as immutable Archiv evidence."""

    return run_ingest(source_path, schema_version)


@mcp.tool(
    name="archiv_search",
    title="Search Archiv with exact citations",
    annotations=READ_ONLY,
)
def archiv_search(
    query: Annotated[str, Field(min_length=1, description="Literal phrase to search for.")],
    source_name: str | None = None,
    media_type: str | None = None,
    kind: str | None = None,
    object_sha256: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Search the local FTS5 index and return only revalidated source citations."""

    return run_search(
        query,
        source_name,
        media_type,
        kind,
        object_sha256,
        limit,
        schema_version,
    )


@mcp.tool(
    name="archiv_read_source",
    title="Read an exact Archiv source excerpt",
    annotations=READ_ONLY,
)
def archiv_read_source(
    citation: Citation,
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Return one normalized excerpt after validating its complete citation envelope."""

    return run_read_source(citation, schema_version)


@mcp.tool(
    name="archiv_generate_docx",
    title="Generate a cited Archiv DOCX report",
    annotations=BOUNDED_WRITE,
)
def archiv_generate_docx(
    query: Annotated[str, Field(min_length=1)],
    output_name: Annotated[
        str,
        Field(description="DOCX basename written under ARCHIV_HOME/outputs/mcp."),
    ],
    title: str = "Archiv Evidence Report",
    max_sources: Annotated[int, Field(ge=1, le=50)] = 8,
    render: bool = False,
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Generate a new cited DOCX inside the fixed MCP output root and validate it."""

    return run_generate_docx(
        query,
        output_name,
        title,
        max_sources,
        render,
        schema_version,
    )


@mcp.tool(
    name="archiv_verify_artifact",
    title="Verify an Archiv MCP report",
    annotations=READ_ONLY,
)
def archiv_verify_artifact(
    output_name: Annotated[
        str,
        Field(description="DOCX basename under ARCHIV_HOME/outputs/mcp."),
    ],
    render: bool = False,
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Revalidate one MCP-owned DOCX and its fixed manifest sidecar."""

    return run_verify_artifact(output_name, render, schema_version)


@mcp.tool(
    name="archiv_get_run_evidence",
    title="Read bounded Archiv MCP run evidence",
    annotations=READ_ONLY,
)
def archiv_get_run_evidence(
    run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
    schema_version: Literal["1"] = "1",
) -> McpRunEnvelope:
    """Read request/result JSON for one prior MCP call without arbitrary file access."""

    return run_get_run_evidence(run_id, schema_version)


MCP_TOOL_HANDLERS = (
    archiv_ingest,
    archiv_search,
    archiv_read_source,
    archiv_generate_docx,
    archiv_verify_artifact,
    archiv_get_run_evidence,
)


def main() -> None:
    """Run the local server over the default stdio transport."""

    mcp.run()


if __name__ == "__main__":
    main()
