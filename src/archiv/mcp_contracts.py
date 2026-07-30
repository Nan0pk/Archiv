"""Versioned contracts for bounded Archiv MCP calls and evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from archiv.contracts import StrictModel

MCP_SCHEMA_VERSION: Literal["1"] = "1"


class McpRunStatus(StrEnum):
    """Terminal states for an MCP tool call."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class McpRunEnvelope(StrictModel):
    """Machine evidence returned by every successful MCP tool call."""

    schema_version: Literal["1"] = MCP_SCHEMA_VERSION
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    tool: str
    status: McpRunStatus
    started_at: str
    finished_at: str
    evidence_dir: str
    network_policy: Literal["denied"] = "denied"
    request: dict[str, object]
    result: dict[str, object]


class McpFailedRun(StrictModel):
    """Persisted evidence for a tool call that raised before returning."""

    schema_version: Literal["1"] = MCP_SCHEMA_VERSION
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    tool: str
    status: Literal["failed"] = "failed"
    started_at: str
    finished_at: str
    evidence_dir: str
    network_policy: Literal["denied"] = "denied"
    request: dict[str, object]
    error_type: str
    error: str


class McpEvidenceResult(StrictModel):
    """A bounded view of one previously recorded MCP call."""

    target_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    target_request: dict[str, object]
    target_result: dict[str, object]
