"""Contracts for pinned CoWork-OS compatibility and regression evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from archiv.contracts import StrictModel


class CoworkStageStatus(StrEnum):
    """Outcome of one independently attributable regression stage."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_EXERCISED = "not_exercised"


class CoworkFaultDomain(StrEnum):
    """Component that owns a failure if a stage does not pass."""

    ARCHIV = "archiv"
    MCP_TRANSPORT = "mcp_transport"
    COWORK_INTEGRATION = "cowork_integration"
    MODEL_PROVIDER = "model_provider"
    WORKBENCH_ORCHESTRATION = "workbench_orchestration"


class CoworkStageResult(StrictModel):
    """One stage with a single explicit fault owner."""

    stage: str
    status: CoworkStageStatus
    owner_if_failed: CoworkFaultDomain
    detail: str


class CoworkRegressionReport(StrictModel):
    """Combined evidence for one pinned or current CoWork revision."""

    schema_version: Literal["1"] = "1"
    mode: Literal["pinned", "current"]
    cowork_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    locked_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    cowork_version: str
    compatible: bool
    upstream_adopted: Literal[False] = False
    source_hashes_unchanged: bool
    exact_task_succeeded: bool
    valid_report_path: str
    evidence_run_ids: list[str] = Field(min_length=1)
    stages: list[CoworkStageResult] = Field(min_length=1)
