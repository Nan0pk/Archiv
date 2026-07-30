"""Filesystem and effect policies for the local Archiv MCP server."""

from __future__ import annotations

import os
from pathlib import Path

from archiv.storage.layout import ArchivLayout

DEFAULT_MAX_SOURCE_BYTES = 100 * 1024 * 1024


def mcp_layout() -> ArchivLayout:
    """Resolve the server-owned Archiv home from its process environment."""

    return ArchivLayout.resolve()


def mcp_runs_root(layout: ArchivLayout) -> Path:
    return layout.root / "runs" / "mcp"


def mcp_outputs_root(layout: ArchivLayout) -> Path:
    return layout.root / "outputs" / "mcp"


def ensure_mcp_roots(layout: ArchivLayout) -> None:
    """Create only the bounded MCP run and output roots."""

    layout.ensure()
    mcp_runs_root(layout).mkdir(parents=True, exist_ok=True)
    mcp_outputs_root(layout).mkdir(parents=True, exist_ok=True)


def _max_source_bytes() -> int:
    raw = os.environ.get("ARCHIV_MCP_MAX_SOURCE_BYTES")
    if raw is None:
        return DEFAULT_MAX_SOURCE_BYTES
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("ARCHIV_MCP_MAX_SOURCE_BYTES must be an integer") from error
    if value < 1:
        raise RuntimeError("ARCHIV_MCP_MAX_SOURCE_BYTES must be positive")
    return value


def validate_ingest_source(source_path: str) -> Path:
    """Accept one explicit, local, non-symlink regular file within the size policy."""

    requested = Path(source_path).expanduser()
    if not requested.is_absolute():
        raise ValueError("MCP ingest source_path must be absolute")
    if requested.is_symlink():
        raise ValueError("MCP ingest rejects symbolic links")
    source = requested.resolve(strict=True)
    if not source.is_file():
        raise ValueError("MCP ingest source must be a regular file")
    if source.stat().st_size > _max_source_bytes():
        raise ValueError("MCP ingest source exceeds ARCHIV_MCP_MAX_SOURCE_BYTES")
    return source


def validate_output_name(output_name: str) -> str:
    """Constrain generated reports to one DOCX basename under the MCP output root."""

    if not output_name or output_name in {".", ".."}:
        raise ValueError("output_name must be a non-empty DOCX basename")
    candidate = Path(output_name)
    if candidate.is_absolute() or candidate.name != output_name:
        raise ValueError("output_name must not contain directories")
    if candidate.suffix.lower() != ".docx":
        raise ValueError("output_name must use the .docx extension")
    return output_name


def report_path(layout: ArchivLayout, output_name: str) -> Path:
    """Resolve a validated report name inside the fixed MCP output root."""

    name = validate_output_name(output_name)
    root = mcp_outputs_root(layout).resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise ValueError("report output escaped the MCP output root")
    return target


def validate_run_id(run_id: str) -> str:
    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise ValueError("run_id must be 32 lowercase hexadecimal characters")
    return run_id
