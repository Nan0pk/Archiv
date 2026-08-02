"""Versioned deterministic task-run and verification contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from archiv.contracts import RetrievalDiagnostics, RunStatus, StrictModel
from archiv.model_adapter import ModelConfig
from archiv.report_contracts import ReportValidation


class CrossFileReportTask(StrictModel):
    """One bounded cross-file report request stored as JSON-compatible YAML."""

    schema_version: str = "1"
    task: Literal["cross-file-report"] = "cross-file-report"
    query: str = Field(min_length=1)
    title: str = "Archiv Cross-file Evidence Report"
    output_name: str = "cross-file-report.docx"
    max_sources: int = Field(default=8, ge=1, le=50)
    render: bool = True
    model_policy: Literal["disabled", "configured-local"] = "disabled"


class TaskRunResult(StrictModel):
    """Terminal task result backed by report and source-integrity evidence."""

    schema_version: str = "1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: RunStatus
    task_path: str
    evidence_dir: str
    output_path: str | None = None
    manifest_path: str | None = None
    validation_path: str | None = None
    source_hashes_before: dict[str, str] = Field(default_factory=dict)
    source_hashes_after: dict[str, str] = Field(default_factory=dict)
    citation_count: int = Field(default=0, ge=0)
    retrieval_diagnostics: RetrievalDiagnostics | None = None
    model: ModelConfig
    network_policy: Literal["denied"] = "denied"
    errors: list[str] = Field(default_factory=list)


class TaskVerification(StrictModel):
    """Independent verification for one prior deterministic task run."""

    schema_version: str = "1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    valid: bool
    errors: list[str] = Field(default_factory=list)
    source_hashes_match: bool
    report_validation: ReportValidation | None = None
