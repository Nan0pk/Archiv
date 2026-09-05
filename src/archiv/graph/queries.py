# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Cross-corpus traversal queries and entity profile retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archiv.graph.contracts import (
    CrossCorpusQueryResult,
    EntityAppearance,
    EntityMention,
    EntityProfile,
    GraphNode,
)
from archiv.graph.storage import (
    connect_graph_index,
    find_node_by_name,
    get_node,
    get_out_edges,
    graph_index_path,
)
from archiv.storage.layout import ArchivLayout


def get_entity_profile(
    name_or_id: str,
    home: Path | None = None,
) -> EntityProfile | None:
    """Retrieve 360-degree cross-corpus profile of an entity with evidence citations."""
    layout = ArchivLayout.resolve(home)
    db_path = graph_index_path(layout)
    if not db_path.is_file():
        return None

    with connect_graph_index(db_path) as conn:
        node = get_node(conn, name_or_id) or find_node_by_name(conn, name_or_id)
        if not node:
            return None

        out_edges = get_out_edges(conn, node.node_id)

        appearances: list[EntityAppearance] = []
        mentions: list[EntityMention] = []
        co_occurrences: list[dict[str, Any]] = []
        associations: list[dict[str, Any]] = []

        for e in out_edges:
            target_node = get_node(conn, e.target_node_id)
            if not target_node:
                continue

            if e.relation_type == "appears_in":
                # Find year from image out-edges
                img_year: int | None = None
                img_edges = get_out_edges(conn, target_node.node_id, "associated_with")
                for ie in img_edges:
                    d_node = get_node(conn, ie.target_node_id)
                    if d_node and d_node.entity_type == "date":
                        img_year = int(d_node.canonical_name)
                        break

                bbox: list[float] | None = None
                for c in e.citations:
                    if "bbox" in c.locator and isinstance(c.locator["bbox"], list):
                        bbox = c.locator["bbox"]
                        break

                appearances.append(
                    EntityAppearance(
                        image_sha256=str(target_node.properties.get("sha256", "")),
                        image_name=target_node.canonical_name,
                        bbox=bbox,
                        year=img_year,
                        confidence=e.confidence,
                        status=e.status,
                        citations=e.citations,
                    )
                )

            elif e.relation_type == "mentioned_in":
                loc = e.citations[0].locator if e.citations else {}
                snip = e.citations[0].snippet if e.citations else ""
                mentions.append(
                    EntityMention(
                        document_sha256=str(target_node.properties.get("sha256", "")),
                        document_name=target_node.canonical_name,
                        locator=loc,
                        snippet=snip,
                        confidence=e.confidence,
                        status=e.status,
                    )
                )

            elif e.relation_type == "co_occurs_with":
                co_occurrences.append(
                    {
                        "entity_name": target_node.canonical_name,
                        "entity_type": target_node.entity_type,
                        "confidence": e.confidence,
                        "citations": [c.model_dump(mode="json") for c in e.citations],
                    }
                )

            elif e.relation_type == "associated_with":
                associations.append(
                    {
                        "target_name": target_node.canonical_name,
                        "target_type": target_node.entity_type,
                        "confidence": e.confidence,
                        "citations": [c.model_dump(mode="json") for c in e.citations],
                    }
                )

        return EntityProfile(
            entity=node,
            appearances=appearances,
            mentions=mentions,
            co_occurrences=co_occurrences,
            associations=associations,
        )


def query_cross_corpus(
    person_name: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    home: Path | None = None,
) -> list[CrossCorpusQueryResult]:
    """Cross-corpus traversal: find persons in photos within date range
    and documents mentioning them.
    """
    layout = ArchivLayout.resolve(home)
    db_path = graph_index_path(layout)
    if not db_path.is_file():
        return []

    results: list[CrossCorpusQueryResult] = []

    with connect_graph_index(db_path) as conn:
        # 1. Fetch person nodes
        if person_name:
            p_node = find_node_by_name(conn, person_name, "person")
            person_nodes: list[GraphNode] = [p_node] if p_node else []
        else:
            rows = conn.execute("SELECT * FROM nodes WHERE entity_type = 'person'").fetchall()
            person_nodes = [
                GraphNode(
                    node_id=str(r["node_id"]),
                    entity_type="person",
                    canonical_name=str(r["canonical_name"]),
                    aliases=[],
                    properties={},
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]

        for p in person_nodes:
            profile = get_entity_profile(p.node_id, home=home)
            if not profile or not profile.appearances:
                continue

            # Filter photograph appearances by date range
            matching_photos: list[EntityAppearance] = []
            for app in profile.appearances:
                if date_from is not None and (app.year is None or app.year < date_from):
                    continue
                if date_to is not None and (app.year is None or app.year > date_to):
                    continue
                matching_photos.append(app)

            # If no photos matched date criteria
            if not matching_photos:
                continue

            # Return persons who appear in photos
            results.append(
                CrossCorpusQueryResult(
                    person_name=p.canonical_name,
                    person_id=p.node_id,
                    status=matching_photos[0].status,
                    photographs=matching_photos,
                    mentioning_documents=profile.mentions,
                )
            )

    return results
