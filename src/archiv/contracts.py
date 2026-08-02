"""Versioned execution, ingestion, retrieval, and evidence contracts."""

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


class IngestionStatus(StrEnum):
    """Terminal ingestion states exposed to clients."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


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


class NormalizedSegment(StrictModel):
    """One text-bearing unit with a format-native source locator."""

    locator: dict[str, object]
    text: str


class NormalizedTable(StrictModel):
    """A portable table plus its sheet or document locator."""

    locator: dict[str, object]
    rows: list[list[object | None]]


class NormalizedDocument(StrictModel):
    """Portable derived representation rebuilt from a canonical original."""

    schema_version: str = "1"
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    kind: str
    source_name: str
    metadata: dict[str, object] = Field(default_factory=dict)
    segments: list[NormalizedSegment] = Field(default_factory=lambda: list[NormalizedSegment]())
    tables: list[NormalizedTable] = Field(default_factory=lambda: list[NormalizedTable]())


class ProcessingEvidence(StrictModel):
    """One processor outcome and its optional derived artifact."""

    processor: str
    processor_version: str
    status: str
    output_kind: str
    output_path: str | None = None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = None


class IngestionResult(StrictModel):
    """Successful ingestion result backed by durable storage and processing rows."""

    schema_version: str = "1"
    ingestion_id: str
    status: IngestionStatus
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    duplicate: bool
    media_type: str
    original_path: str
    derived_root: str
    source_hash_unchanged: bool
    processing: list[ProcessingEvidence]


class Citation(StrictModel):
    """Exact source and normalized-segment evidence for one retrieved passage."""

    schema_version: str = "1"
    segment_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_index: int = Field(ge=0)
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_name: str
    media_type: str
    kind: str
    locator: dict[str, object]
    normalized_path: str
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SearchResult(StrictModel):
    """One validated full-text result."""

    text: str
    rank: float
    citation: Citation


class RetrievalQueryVariant(StrictModel):
    """One bounded literal query used by deterministic evidence retrieval."""

    kind: str
    query: str
    weight: int = Field(ge=1)
    result_count: int = Field(ge=0)


class RetrievalSelection(StrictModel):
    """Explainable source selection made after merging query variants."""

    segment_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_name: str
    locator: dict[str, object]
    rank: float
    score: float
    matched_queries: list[str] = Field(default_factory=list)


class RetrievalDiagnostics(StrictModel):
    """Versioned local diagnostics for one natural-language retrieval decision."""

    schema_version: str = "1"
    strategy_version: str = "deterministic-literal-expansion-v1"
    original_objective: str
    derived_terms: list[str] = Field(default_factory=list)
    triggered_concepts: list[str] = Field(default_factory=list)
    query_variants: list[RetrievalQueryVariant] = Field(default_factory=list)
    evidence_limit: int = Field(ge=1, le=50)
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    selections: list[RetrievalSelection] = Field(default_factory=list)


class EvidenceRetrieval(StrictModel):
    """Validated evidence package plus its deterministic retrieval explanation."""

    results: list[SearchResult] = Field(default_factory=list)
    diagnostics: RetrievalDiagnostics


class CitationValidation(StrictModel):
    """Independent citation integrity result."""

    valid: bool
    errors: list[str] = Field(default_factory=lambda: list[str]())


class SearchIndexBuild(StrictModel):
    """Evidence for one atomic search-index rebuild."""

    index_path: str
    index_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_count: int = Field(ge=0)
    segment_count: int = Field(ge=0)
