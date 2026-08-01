"""Contracts for strict model grounding responses."""

from __future__ import annotations

from pydantic import Field

from archiv.contracts import StrictModel


class GroundedClaim(StrictModel):
    """One claim statement with exact citation identifiers."""

    claim_id: str
    statement: str
    citation_ids: list[str] = Field(default_factory=list)


class GroundedParagraph(StrictModel):
    """One answer paragraph referencing claims and citations."""

    paragraph_id: str
    text: str
    citation_ids: list[str] = Field(default_factory=list)


class GroundedModelResponse(StrictModel):
    """Strict response contract required from the local model."""

    schema_version: str = "1"
    paragraphs: list[GroundedParagraph] = Field(default_factory=list)
    claims: list[GroundedClaim] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
