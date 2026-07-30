"""Bounded local operations exposed by the Archiv MCP server."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel

from archiv.contracts import Citation
from archiv.ingestion import ingest_file
from archiv.mcp_contracts import MCP_SCHEMA_VERSION, McpEvidenceResult, McpRunEnvelope
from archiv.mcp_ledger import execute_tool, read_run_record
from archiv.mcp_policy import (
    ensure_mcp_roots,
    mcp_layout,
    report_path,
    validate_ingest_source,
    validate_run_id,
)
from archiv.report_contracts import ReportStatus
from archiv.reports import generate_report, validate_report
from archiv.search import read_source_excerpt, search_documents

SchemaVersion = Literal["1"]


def _require_schema_version(schema_version: str) -> None:
    if schema_version != MCP_SCHEMA_VERSION:
        raise ValueError(f"unsupported MCP schema_version: {schema_version}")


def _model_payload(model: BaseModel) -> dict[str, object]:
    return cast(dict[str, object], model.model_dump(mode="json"))


def archiv_ingest(
    source_path: str,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Ingest one explicit absolute local file into immutable Archiv storage."""

    _require_schema_version(schema_version)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "source_path": source_path,
    }

    def operation() -> dict[str, object]:
        source = validate_ingest_source(source_path)
        result = ingest_file(source, home=layout.root)
        return {"ingestion": _model_payload(result)}

    return execute_tool(layout, "archiv_ingest", request, operation)


def archiv_search(
    query: str,
    source_name: str | None = None,
    media_type: str | None = None,
    kind: str | None = None,
    object_sha256: str | None = None,
    limit: int = 20,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Search literal local text and return independently validated citations."""

    _require_schema_version(schema_version)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "query": query,
        "source_name": source_name,
        "media_type": media_type,
        "kind": kind,
        "object_sha256": object_sha256,
        "limit": limit,
    }

    def operation() -> dict[str, object]:
        results = search_documents(
            query,
            home=layout.root,
            source_name=source_name,
            media_type=media_type,
            kind=kind,
            object_sha256=object_sha256,
            limit=limit,
        )
        return {
            "count": len(results),
            "results": [result.model_dump(mode="json") for result in results],
        }

    return execute_tool(layout, "archiv_search", request, operation)


def archiv_read_source(
    citation: Citation,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Read one exact excerpt only after revalidating its complete citation."""

    _require_schema_version(schema_version)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "citation": citation.model_dump(mode="json"),
    }

    def operation() -> dict[str, object]:
        excerpt = read_source_excerpt(citation, home=layout.root)
        return {
            "citation": citation.model_dump(mode="json"),
            "excerpt": excerpt,
        }

    return execute_tool(layout, "archiv_read_source", request, operation)


def archiv_generate_docx(
    query: str,
    output_name: str,
    title: str = "Archiv Evidence Report",
    max_sources: int = 8,
    render: bool = False,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Generate one validated DOCX inside the fixed MCP output directory."""

    _require_schema_version(schema_version)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "query": query,
        "output_name": output_name,
        "title": title,
        "max_sources": max_sources,
        "render": render,
    }

    def operation() -> dict[str, object]:
        ensure_mcp_roots(layout)
        output = report_path(layout, output_name)
        sidecars = (
            output,
            output.with_suffix(output.suffix + ".manifest.json"),
            output.with_suffix(output.suffix + ".validation.json"),
        )
        if any(path.exists() for path in sidecars):
            raise FileExistsError("MCP report output or sidecar already exists")
        result = generate_report(
            query,
            output,
            title=title,
            home=layout.root,
            max_sources=max_sources,
            render=render,
            evidence_dir=output.parent if render else None,
        )
        if result.status is not ReportStatus.SUCCEEDED or not result.validation.valid:
            raise RuntimeError("generated DOCX failed independent validation")
        return {"report": _model_payload(result)}

    return execute_tool(layout, "archiv_generate_docx", request, operation)


def archiv_verify_artifact(
    output_name: str,
    render: bool = False,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Verify one MCP-owned DOCX and its fixed manifest sidecar."""

    _require_schema_version(schema_version)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "output_name": output_name,
        "render": render,
    }

    def operation() -> dict[str, object]:
        output = report_path(layout, output_name)
        manifest = output.with_suffix(output.suffix + ".manifest.json")
        validation = validate_report(
            output,
            manifest,
            home=layout.root,
            render=render,
            evidence_dir=output.parent if render else None,
        )
        if not validation.valid:
            raise ValueError("artifact validation failed: " + "; ".join(validation.errors))
        return {"validation": _model_payload(validation)}

    return execute_tool(layout, "archiv_verify_artifact", request, operation)


def archiv_get_run_evidence(
    run_id: str,
    schema_version: SchemaVersion = MCP_SCHEMA_VERSION,
) -> McpRunEnvelope:
    """Read bounded request/result JSON for one prior MCP call."""

    _require_schema_version(schema_version)
    target_run_id = validate_run_id(run_id)
    layout = mcp_layout()
    request: dict[str, object] = {
        "schema_version": schema_version,
        "run_id": target_run_id,
    }

    def operation() -> dict[str, object]:
        target_request, target_result = read_run_record(layout, target_run_id)
        evidence = McpEvidenceResult(
            target_run_id=target_run_id,
            target_request=target_request,
            target_result=target_result,
        )
        return {"evidence": _model_payload(evidence)}

    return execute_tool(layout, "archiv_get_run_evidence", request, operation)
