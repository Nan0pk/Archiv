"""Schemas and contracts for the evidence-backed entity graph."""

from __future__ import annotations

from typing import Any, Literal

from archiv.contracts import StrictModel

NodeType = Literal["person", "document", "image", "date", "location", "organization"]
RelationType = Literal["appears_in", "mentioned_in", "co_occurs_with", "associated_with"]
ConfidenceStatus = Literal["confirmed", "probable", "possible"]


class GraphCitation(StrictModel):
    """Granular evidence citation justifying a node or edge."""

    object_sha256: str
    source_name: str
    locator: dict[str, Any]
    snippet: str


class GraphNode(StrictModel):
    """An entity node in the rebuildable graph."""

    node_id: str
    entity_type: NodeType
    canonical_name: str
    aliases: list[str] = []
    properties: dict[str, Any] = {}
    created_at: str


class GraphEdge(StrictModel):
    """An evidence-justified edge between entities."""

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: RelationType
    confidence: float
    status: ConfidenceStatus
    citations: list[GraphCitation]
    created_at: str


class EntityAppearance(StrictModel):
    """Visual appearance of an entity in an image."""

    image_sha256: str
    image_name: str
    bbox: list[float] | None = None
    year: int | None = None
    confidence: float
    status: ConfidenceStatus
    citations: list[GraphCitation]


class EntityMention(StrictModel):
    """Textual mention of an entity in a document."""

    document_sha256: str
    document_name: str
    locator: dict[str, Any]
    snippet: str
    confidence: float
    status: ConfidenceStatus


class EntityProfile(StrictModel):
    """360-degree cross-corpus profile of an entity."""

    entity: GraphNode
    appearances: list[EntityAppearance] = []
    mentions: list[EntityMention] = []
    co_occurrences: list[dict[str, Any]] = []
    associations: list[dict[str, Any]] = []


class CrossCorpusQueryResult(StrictModel):
    """Result of a cross-corpus entity and evidence traversal query."""

    person_name: str
    person_id: str
    status: ConfidenceStatus
    photographs: list[EntityAppearance]
    mentioning_documents: list[EntityMention]
