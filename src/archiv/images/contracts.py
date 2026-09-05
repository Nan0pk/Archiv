"""Contracts for image embeddings, semantic image search, and near-duplicate detection."""

from __future__ import annotations

from archiv.contracts import StrictModel


class ImageSearchResult(StrictModel):
    """One ranked image retrieval result."""

    object_sha256: str
    score: float
    source_name: str
    media_type: str
    width: int
    height: int
    original_path: str
    preview_path: str | None = None


class ImageIndexBuildResult(StrictModel):
    """Summary of an atomic image embedding index build."""

    object_count: int
    index_size_bytes: int
    elapsed_seconds: float
    model_name: str
    index_path: str


class NearDuplicateItem(StrictModel):
    """An image member of a near-duplicate cluster."""

    object_sha256: str
    source_name: str
    similarity_to_lead: float


class NearDuplicateGroup(StrictModel):
    """A cluster of images identified as near-duplicates."""

    lead_sha256: str
    lead_source_name: str
    members: list[NearDuplicateItem]
    max_similarity: float
