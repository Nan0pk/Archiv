"""Contracts and schemas for face detection, clustering, and name attributions."""

from __future__ import annotations

from typing import Literal

from archiv.contracts import StrictModel


class FaceConfig(StrictModel):
    """Opt-in configuration for biometric and face analysis."""

    opt_in: bool = False
    opt_in_at: str | None = None
    similarity_threshold: float = 0.96
    min_detection_confidence: float = 0.50


class FaceDetection(StrictModel):
    """A detected face region in an image."""

    face_id: str
    object_sha256: str
    source_name: str
    bbox: list[float]  # [x0, y0, x1, y1]
    confidence: float
    embedding: list[float]  # L2-normalized vector


class FaceCluster(StrictModel):
    """An unnamed cluster representing one distinct individual."""

    cluster_id: str
    label: str  # e.g. "Person 1"
    member_count: int
    centroid: list[float]
    created_at: str
    updated_at: str


class EvidenceCitation(StrictModel):
    """Citation linking an attribution hypothesis to durable evidence."""

    source_type: Literal["exif", "iptc", "filename", "caption", "co_located_text"]
    source_name: str
    object_sha256: str
    detail: str
    weight: float


class CandidateName(StrictModel):
    """A scored, cited name hypothesis for a face cluster."""

    name: str
    confidence: float
    supporting_citations: list[EvidenceCitation]
    contradicting_citations: list[EvidenceCitation]


class ClusterAttribution(StrictModel):
    """Complete attribution analysis for a face cluster."""

    cluster_id: str
    label: str
    member_count: int
    status: Literal["unconfirmed", "confirmed"]
    confirmed_name: str | None = None
    confirmed_at: str | None = None
    candidates: list[CandidateName]
