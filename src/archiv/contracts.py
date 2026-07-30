"""Versioned execution and evidence contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """Terminal states allowed by the execution contract."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PARTIALLY_PRODUCED_BUT_INVALID = "partially_produced_but_invalid"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class StrictModel(BaseModel):
    """Base class for stable machine-readable contracts."""

    model_config = ConfigDict(extra="forbid")


class FileEvidence(StrictModel):
    """Digest and size evidence for one file."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class SourceHashes(StrictModel):
    """Source integrity evidence before and after execution."""

    before: FileEvidence
    after: FileEvidence | None = None


class FileChanges(StrictModel):
    """Declared effects of the source-marker capability."""

    created: list[str] = Field(default_factory=list)
    replaced: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class ValidationReport(StrictModel):
    """Independent validator result."""

    passed: bool
    errors: list[str] = Field(default_factory=list)
    expected_output_sha256: str | None = None
    actual_output: FileEvidence | None = None


class SourceMarkerRequest(StrictModel):
    """Request envelope for the first deterministic capability."""

    contract_version: str = "1"
    capability: str = "source-marker"
    workspace: str
    source_path: str = "source.txt"
    output_path: str = "outputs/probe.txt"


class RunResult(StrictModel):
    """Terminal result backed by validator evidence."""

    contract_version: str = "1"
    run_id: str
    status: RunStatus
    output_path: str | None
    evidence_dir: str
    validation: ValidationReport
