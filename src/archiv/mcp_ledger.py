"""Append-only per-call evidence for the Archiv MCP boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast
from uuid import uuid4

from archiv.mcp_contracts import McpFailedRun, McpRunEnvelope, McpRunStatus
from archiv.mcp_policy import ensure_mcp_roots, mcp_runs_root, validate_run_id
from archiv.storage.layout import ArchivLayout

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class McpRunContext:
    run_id: str
    tool: str
    started_at: str
    evidence_dir: Path
    request: dict[str, object]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def begin_run(layout: ArchivLayout, tool: str, request: dict[str, object]) -> McpRunContext:
    """Create a unique append-only run directory and persist its request."""

    ensure_mcp_roots(layout)
    run_id = uuid4().hex
    evidence_dir = mcp_runs_root(layout) / run_id
    evidence_dir.mkdir(parents=False, exist_ok=False)
    started_at = _now()
    request_record: dict[str, object] = {
        "schema_version": "1",
        "run_id": run_id,
        "tool": tool,
        "started_at": started_at,
        "network_policy": "denied",
        "request": request,
    }
    _write_json(evidence_dir / "request.json", request_record)
    return McpRunContext(
        run_id=run_id,
        tool=tool,
        started_at=started_at,
        evidence_dir=evidence_dir,
        request=request,
    )


def finish_success(context: McpRunContext, result: dict[str, object]) -> McpRunEnvelope:
    envelope = McpRunEnvelope(
        run_id=context.run_id,
        tool=context.tool,
        status=McpRunStatus.SUCCEEDED,
        started_at=context.started_at,
        finished_at=_now(),
        evidence_dir=str(context.evidence_dir),
        request=context.request,
        result=result,
    )
    _write_json(
        context.evidence_dir / "result.json",
        cast(dict[str, object], envelope.model_dump(mode="json")),
    )
    return envelope


def finish_failure(context: McpRunContext, error: Exception) -> McpFailedRun:
    failure = McpFailedRun(
        run_id=context.run_id,
        tool=context.tool,
        started_at=context.started_at,
        finished_at=_now(),
        evidence_dir=str(context.evidence_dir),
        request=context.request,
        error_type=type(error).__name__,
        error=str(error),
    )
    _write_json(
        context.evidence_dir / "result.json",
        cast(dict[str, object], failure.model_dump(mode="json")),
    )
    return failure


def execute_tool(
    layout: ArchivLayout,
    tool: str,
    request: dict[str, object],
    operation: Callable[[], dict[str, object]],
) -> McpRunEnvelope:
    """Execute one bounded operation and persist failure before re-raising it."""

    context = begin_run(layout, tool, request)
    try:
        result = operation()
    except Exception as error:
        finish_failure(context, error)
        raise
    return finish_success(context, result)


def read_run_record(layout: ArchivLayout, run_id: str) -> tuple[dict[str, object], dict[str, object]]:
    """Read only the two bounded JSON records for one MCP run."""

    safe_run_id = validate_run_id(run_id)
    root = mcp_runs_root(layout).resolve()
    evidence_dir = (root / safe_run_id).resolve()
    if not evidence_dir.is_relative_to(root):
        raise ValueError("run evidence path escaped the MCP run root")
    request_path = evidence_dir / "request.json"
    result_path = evidence_dir / "result.json"
    if not request_path.is_file() or not result_path.is_file():
        raise FileNotFoundError(f"unknown or incomplete MCP run: {safe_run_id}")
    request = cast(
        dict[str, object],
        json.loads(request_path.read_text(encoding="utf-8")),
    )
    result = cast(
        dict[str, object],
        json.loads(result_path.read_text(encoding="utf-8")),
    )
    return request, result
