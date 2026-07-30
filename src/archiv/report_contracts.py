"""Versioned report-generation and rendering contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from archiv.contracts import Citation, FileEvidence, StrictModel

REQUIRED_REPORT_SECTIONS = ("Executive Summary", "Findings", "Source Appendix", "Provenance")


class ReportStatus(StrEnum):
    """Terminal report-generation states."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReportSource(StrictModel):
    """One numbered source included in a generated report."""

    number: int = Field(ge=1)
    citation: Citation
    excerpt: str
    locator_text: str


class ReportManifest(StrictModel):
    """Sidecar provenance tying a DOCX to exact Archiv sources."""

    schema_version: str = "1"
    report_id: str
    title: str
    query: str
    generated_at: str
    docx_path: str
    docx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_sections: list[str] = Field(min_length=1)
    sources: list[ReportSource] = Field(min_length=1)


class ReportValidation(StrictModel):
    """Independent package, citation, and rendering validation evidence."""

    valid: bool
    errors: list[str] = Field(default_factory=lambda: list[str]())
    warnings: list[str] = Field(default_factory=lambda: list[str]())
    docx_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pdf_path: str | None = None
    pdf_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    extracted_text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(default=0, ge=0)
    page_images: list[FileEvidence] = Field(default_factory=lambda: list[FileEvidence]())


class ReportGenerationResult(StrictModel):
    """Terminal report result; success is derived only from validator evidence."""

    status: ReportStatus
    report_id: str
    docx_path: str
    manifest_path: str
    validation_path: str
    validation: ReportValidation
