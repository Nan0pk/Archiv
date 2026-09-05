# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Rebuildable graph index builder constructing nodes and evidence-backed edges."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from archiv.faces.attributions import attribute_cluster
from archiv.faces.storage import connect_face_index, face_index_path, get_confirmation
from archiv.graph.contracts import (
    ConfidenceStatus,
    GraphCitation,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationType,
)
from archiv.graph.storage import connect_graph_index, graph_index_path, save_edge, save_node
from archiv.storage.layout import ArchivLayout

_DISALLOWED_NAMES = {
    "united states",
    "new york",
    "san francisco",
    "high school",
    "page number",
    "table contents",
    "figure one",
    "chapter two",
    "all rights",
    "terms service",
    "public license",
    "apache license",
    "north america",
    "south america",
    "east coast",
    "west coast",
    "hong kong",
    "los angeles",
    "great britain",
    "united kingdom",
}

_DISALLOWED_WORDS = {
    "engine",
    "computer",
    "report",
    "system",
    "history",
    "algorithm",
    "notes",
    "chapter",
    "section",
    "table",
    "figure",
    "page",
    "model",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}


def _clean_person_name(name: str) -> str | None:
    cleaned = name.strip()
    words = cleaned.split()
    if not (2 <= len(words) <= 3):
        return None
    for w in words:
        if not re.match(r"^[A-Z][a-z]+$", w):
            return None
        if w.lower() in _DISALLOWED_WORDS:
            return None
    if cleaned.lower() in _DISALLOWED_NAMES:
        return None
    return " ".join(w.capitalize() for w in words)


def _hash_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def rebuild_graph(home: Path | None = None) -> tuple[int, int]:
    """Rebuild the SQLite entity graph from canonical objects, derived segments, and faces.

    Returns (total_nodes_created, total_edges_created).
    """
    layout = ArchivLayout.resolve(home)
    layout.ensure()

    final_path = graph_index_path(layout)
    tmp_path = layout.temporary / f"graph-{uuid4().hex[:12]}.sqlite3.tmp"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).isoformat()
    nodes_by_id: dict[str, GraphNode] = {}
    edges_by_id: dict[str, GraphEdge] = {}

    def _get_or_create_node(
        entity_type: NodeType,
        canonical_name: str,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        nid = _hash_id(entity_type[:3], canonical_name)
        if nid in nodes_by_id:
            return nodes_by_id[nid]
        node = GraphNode(
            node_id=nid,
            entity_type=entity_type,
            canonical_name=canonical_name,
            aliases=[],
            properties=properties or {},
            created_at=now,
        )
        nodes_by_id[nid] = node
        return node

    def _add_edge(
        source_node_id: str,
        target_node_id: str,
        relation_type: RelationType,
        confidence: float,
        status: ConfidenceStatus,
        citations: list[GraphCitation],
    ) -> None:
        # Edge key based on endpoints and relation
        ekey = f"{source_node_id}::{relation_type}::{target_node_id}"
        eid = _hash_id("edge", ekey)
        if eid in edges_by_id:
            # Merge citations and update confidence if higher
            existing = edges_by_id[eid]
            new_conf = max(existing.confidence, confidence)
            new_cits = list(existing.citations)
            for c in citations:
                if not any(
                    ec.object_sha256 == c.object_sha256 and ec.locator == c.locator
                    for ec in new_cits
                ):
                    new_cits.append(c)
            is_confirmed = existing.status == "confirmed" or status == "confirmed"
            edge_status: ConfidenceStatus = "confirmed" if is_confirmed else status
            edges_by_id[eid] = GraphEdge(
                edge_id=eid,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=relation_type,
                confidence=round(new_conf, 3),
                status=edge_status,
                citations=new_cits,
                created_at=existing.created_at,
            )
        else:
            edges_by_id[eid] = GraphEdge(
                edge_id=eid,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=relation_type,
                confidence=round(confidence, 3),
                status=status,
                citations=citations,
                created_at=now,
            )

    # 1. Read Canonical Objects
    if layout.database.is_file():
        with sqlite3.connect(layout.database) as db_conn:
            db_conn.row_factory = sqlite3.Row
            rows = db_conn.execute(
                """
                SELECT o.sha256, o.media_type, COALESCE(i.source_name, 'unknown') as source_name
                FROM objects o
                LEFT JOIN ingestions i ON o.sha256 = i.object_sha256
                GROUP BY o.sha256
                """
            ).fetchall()

            for r in rows:
                sha256 = str(r["sha256"])
                media_type = str(r["media_type"])
                source_name = str(r["source_name"])
                is_image = media_type.startswith("image/")

                # Create document / image node
                entity_type: NodeType = "image" if is_image else "document"
                obj_node = _get_or_create_node(
                    entity_type,
                    canonical_name=source_name,
                    properties={"sha256": sha256, "media_type": media_type},
                )

                # Check year in filename / metadata
                year_match = re.search(r"(?:^|[\W_])(18\d\d|19\d\d|20\d\d)(?:$|[\W_])", source_name)
                obj_year: int | None = int(year_match.group(1)) if year_match else None

                if obj_year:
                    date_node = _get_or_create_node(
                        "date", canonical_name=str(obj_year), properties={"year": obj_year}
                    )
                    _add_edge(
                        source_node_id=obj_node.node_id,
                        target_node_id=date_node.node_id,
                        relation_type="associated_with",
                        confidence=0.90,
                        status="probable",
                        citations=[
                            GraphCitation(
                                object_sha256=sha256,
                                source_name=source_name,
                                locator={"filename": source_name},
                                snippet=f"Year {obj_year} found in filename '{source_name}'",
                            )
                        ],
                    )

                # Inspect derived document segments
                derived_file = layout.derived_root(sha256) / "normalized" / "document.json"
                if derived_file.is_file():
                    try:
                        doc_payload = json.loads(derived_file.read_text(encoding="utf-8"))
                        segments = doc_payload.get("segments", [])

                        for idx, seg in enumerate(segments):
                            seg_text = seg.get("text", "")
                            seg_loc = seg.get("locator", {"segment_index": idx})

                            # Extract names
                            raw_names = re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", seg_text)
                            person_nodes: list[GraphNode] = []
                            for rn in raw_names:
                                pn = _clean_person_name(rn)
                                if pn:
                                    p_node = _get_or_create_node("person", canonical_name=pn)
                                    person_nodes.append(p_node)

                                    # Edge: Person mentioned_in Document
                                    snip_start = max(0, seg_text.find(rn) - 20)
                                    snip_end = min(len(seg_text), snip_start + 100)
                                    snippet = seg_text[snip_start:snip_end].strip()

                                    _add_edge(
                                        source_node_id=p_node.node_id,
                                        target_node_id=obj_node.node_id,
                                        relation_type="mentioned_in",
                                        confidence=0.85,
                                        status="probable",
                                        citations=[
                                            GraphCitation(
                                                object_sha256=sha256,
                                                source_name=source_name,
                                                locator=seg_loc,
                                                snippet=snippet,
                                            )
                                        ],
                                    )

                            # Extract dates in segment
                            seg_years = re.findall(
                                r"(?:^|[\W_])(18\d\d|19\d\d|20\d\d)(?:$|[\W_])", seg_text
                            )
                            for sy in seg_years:
                                y_val = int(sy)
                                d_node = _get_or_create_node(
                                    "date",
                                    canonical_name=str(y_val),
                                    properties={"year": y_val},
                                )
                                for p_node in person_nodes:
                                    _add_edge(
                                        source_node_id=p_node.node_id,
                                        target_node_id=d_node.node_id,
                                        relation_type="associated_with",
                                        confidence=0.80,
                                        status="probable",
                                        citations=[
                                            GraphCitation(
                                                object_sha256=sha256,
                                                source_name=source_name,
                                                locator=seg_loc,
                                                snippet=(
                                                    f"Mentioned in {source_name} alongside "
                                                    f"year {y_val}"
                                                ),
                                            )
                                        ],
                                    )

                            # Co-occurrences between persons in same segment
                            for i in range(len(person_nodes)):
                                for j in range(i + 1, len(person_nodes)):
                                    p1, p2 = person_nodes[i], person_nodes[j]
                                    if p1.node_id != p2.node_id:
                                        _add_edge(
                                            source_node_id=p1.node_id,
                                            target_node_id=p2.node_id,
                                            relation_type="co_occurs_with",
                                            confidence=0.75,
                                            status="probable",
                                            citations=[
                                                GraphCitation(
                                                    object_sha256=sha256,
                                                    source_name=source_name,
                                                    locator=seg_loc,
                                                    snippet=(
                                                        f"Co-occur in {source_name}: "
                                                        f"'{p1.canonical_name}' and "
                                                        f"'{p2.canonical_name}'"
                                                    ),
                                                )
                                            ],
                                        )
                                        _add_edge(
                                            source_node_id=p2.node_id,
                                            target_node_id=p1.node_id,
                                            relation_type="co_occurs_with",
                                            confidence=0.75,
                                            status="probable",
                                            citations=[
                                                GraphCitation(
                                                    object_sha256=sha256,
                                                    source_name=source_name,
                                                    locator=seg_loc,
                                                    snippet=(
                                                        f"Co-occur in {source_name}: "
                                                        f"'{p2.canonical_name}' and "
                                                        f"'{p1.canonical_name}'"
                                                    ),
                                                )
                                            ],
                                        )
                    except Exception:
                        pass

    # 2. Read Face Detections, Clusters, and Confirmations
    f_db = face_index_path(layout)
    if f_db.is_file():
        try:
            with connect_face_index(f_db) as f_conn:
                clusters = f_conn.execute("SELECT cluster_id, label FROM face_clusters").fetchall()
                for c_row in clusters:
                    cid = str(c_row["cluster_id"])
                    label = str(c_row["label"])

                    conf = get_confirmation(f_conn, cid)
                    if conf:
                        person_name = conf[0]
                        status: ConfidenceStatus = "confirmed"
                        p_conf = 1.0
                    else:
                        attr = attribute_cluster(layout, cid, label)
                        if attr and attr.candidates and attr.candidates[0].confidence >= 0.50:
                            person_name = attr.candidates[0].name
                            status = "probable"
                            p_conf = attr.candidates[0].confidence
                        else:
                            person_name = label
                            status = "possible"
                            p_conf = 0.50

                    person_node = _get_or_create_node("person", canonical_name=person_name)

                    # Fetch member faces
                    m_rows = f_conn.execute(
                        """
                        SELECT face_id, object_sha256, source_name, bbox_json, confidence
                        FROM faces
                        WHERE cluster_id = ?
                        """,
                        (cid,),
                    ).fetchall()

                    for mr in m_rows:
                        img_sha = str(mr["object_sha256"])
                        src_name = str(mr["source_name"])
                        bbox = json.loads(str(mr["bbox_json"]))
                        det_conf = float(mr["confidence"])

                        img_node = _get_or_create_node(
                            "image",
                            canonical_name=src_name,
                            properties={"sha256": img_sha},
                        )

                        # Edge: Person appears_in Image
                        _add_edge(
                            source_node_id=person_node.node_id,
                            target_node_id=img_node.node_id,
                            relation_type="appears_in",
                            confidence=round(p_conf * det_conf, 3),
                            status=status,
                            citations=[
                                GraphCitation(
                                    object_sha256=img_sha,
                                    source_name=src_name,
                                    locator={"bbox": bbox},
                                    snippet=(
                                        f"Detected face in photograph '{src_name}' "
                                        f"(conf: {det_conf:.2f})"
                                    ),
                                )
                            ],
                        )

                        # Check if this image has an associated year
                        img_edges = [
                            e
                            for e in edges_by_id.values()
                            if e.source_node_id == img_node.node_id
                            and e.relation_type == "associated_with"
                        ]
                        for ie in img_edges:
                            _add_edge(
                                source_node_id=person_node.node_id,
                                target_node_id=ie.target_node_id,
                                relation_type="associated_with",
                                confidence=round(p_conf * 0.90, 3),
                                status=status,
                                citations=ie.citations,
                            )
        except Exception:
            pass

    # 3. Write into SQLite database atomically
    with connect_graph_index(tmp_path) as g_conn:
        for node in nodes_by_id.values():
            save_node(g_conn, node)
        for edge in edges_by_id.values():
            save_edge(g_conn, edge)
        g_conn.commit()

    os.replace(tmp_path, final_path)
    return len(nodes_by_id), len(edges_by_id)
