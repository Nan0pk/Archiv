"""Versioned contract for grounded question runs."""

from __future__ import annotations

from pydantic import Field

from archiv.contracts import Citation, RetrievalDiagnostics, RunStatus, StrictModel
from archiv.model_adapter import ModelConfig


def _default_citations() -> list[Citation]:
    return []


def _default_errors() -> list[str]:
    return []


class AskRunResult(StrictModel):
    """Terminal evidence for one grounded ask run."""

    schema_version: str = "1"
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: RunStatus
    query: str
    evidence_dir: str
    model: ModelConfig
    retrieved_citations: list[Citation] = Field(default_factory=_default_citations)
    retrieval_diagnostics: RetrievalDiagnostics | None = None
    raw_model_response: str | None = None
    grounded_response: dict[str, object] | None = None
    errors: list[str] = Field(default_factory=_default_errors)
