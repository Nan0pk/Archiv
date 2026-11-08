# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Rebuildable graph index builder constructing nodes and evidence-backed edges."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
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
from archiv.graph.fingerprint import graph_inputs_fingerprint
from archiv.graph.storage import (
    FINGERPRINT_KEY,
    GRAPH_SCHEMA,
    add_node_if_absent,
    connect_graph_index,
    delete_edge,
    delete_node,
    get_edge,
    get_edges_from,
    get_edges_of_node,
    get_graph_stats,
    graph_index_path,
    node_exists,
    upsert_edge,
)
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


# Edges held in memory are written to the working file after every document, and sooner when
# this many are waiting. Memory therefore follows the largest single document, not the size of
# the whole collection (it used to hold every node and edge until the very end).
_FLUSH_EDGES = 20_000
_CACHE_KIB = 65_536
_LOOKUP_INDEXES = ("idx_nodes_type_name", "idx_edges_source", "idx_edges_target", "idx_edges_rel")


class _WorkFileError(Exception):
    """The working file could not be read or written, so the rebuild cannot be trusted.

    This is kept apart from the errors a single unreadable document may raise: those skip
    the document, this stops the whole rebuild.
    """


@contextmanager
def _working_file() -> Generator[None]:
    try:
        yield
    except sqlite3.Error as exc:
        raise _WorkFileError(str(exc)) from exc


class _Work:
    """The graph being built. It lives in a working file; memory holds only what is not written.

    Edges are merged exactly as they were when the whole graph sat in memory: the highest
    confidence wins, a citation is added unless that document and place is already cited,
    and an edge that has been confirmed stays confirmed.
    """

    def __init__(self, connection: sqlite3.Connection, now: str) -> None:
        self._db = connection
        self._now = now
        self._new_nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}

    def node(
        self,
        entity_type: NodeType,
        canonical_name: str,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        """Make sure the node exists. One that is already stored is left as it was stored."""
        nid = _hash_id(entity_type[:3], canonical_name)
        node = self._new_nodes.get(nid)
        if node is None:
            node = GraphNode(
                node_id=nid,
                entity_type=entity_type,
                canonical_name=canonical_name,
                aliases=[],
                properties=properties or {},
                created_at=self._now,
            )
            self._new_nodes[nid] = node
        return node

    def edge(
        self,
        source_node_id: str,
        target_node_id: str,
        relation_type: RelationType,
        confidence: float,
        status: ConfidenceStatus,
        citations: list[GraphCitation],
    ) -> None:
        ekey = f"{source_node_id}::{relation_type}::{target_node_id}"
        eid = _hash_id("edge", ekey)
        existing = self._edges.get(eid)
        if existing is None:
            with _working_file():
                existing = get_edge(self._db, eid)
        if existing is not None:
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
            self._edges[eid] = GraphEdge(
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
            self._edges[eid] = GraphEdge(
                edge_id=eid,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=relation_type,
                confidence=round(confidence, 3),
                status=status,
                citations=citations,
                created_at=self._now,
            )
        if len(self._edges) >= _FLUSH_EDGES:
            self.flush()

    def flush(self) -> None:
        """Write what is waiting in memory to the working file and forget it."""
        with _working_file():
            for node in self._new_nodes.values():
                add_node_if_absent(self._db, node)
            for edge in self._edges.values():
                upsert_edge(self._db, edge)
            self._db.commit()
        self._new_nodes.clear()
        self._edges.clear()

    def has_node(self, node_id: str) -> bool:
        self.flush()
        with _working_file():
            return node_exists(self._db, node_id)

    def edges_of(self, node_id: str) -> list[GraphEdge]:
        self.flush()
        with _working_file():
            return get_edges_of_node(self._db, node_id)

    def edges_from(self, node_id: str, relation_type: RelationType) -> list[GraphEdge]:
        self.flush()
        with _working_file():
            return get_edges_from(self._db, node_id, relation_type)

    def drop_edge(self, edge_id: str) -> None:
        with _working_file():
            delete_edge(self._db, edge_id)

    def drop_node(self, node_id: str) -> None:
        with _working_file():
            delete_node(self._db, node_id)


def _promote_candidate_edges(work: _Work, person_node: GraphNode) -> None:
    """Attach text evidence to an explicitly confirmed person with the same name."""
    candidate_id = _hash_id("can", person_node.canonical_name)
    if not work.has_node(candidate_id):
        return
    for edge in work.edges_of(candidate_id):
        work.drop_edge(edge.edge_id)
        source_id = (
            person_node.node_id if edge.source_node_id == candidate_id else edge.source_node_id
        )
        target_id = (
            person_node.node_id if edge.target_node_id == candidate_id else edge.target_node_id
        )
        if source_id == target_id:
            continue
        work.edge(
            source_id,
            target_id,
            edge.relation_type,
            edge.confidence,
            edge.status,
            edge.citations,
        )
    work.drop_node(candidate_id)


def _add_document_text(
    work: _Work, obj_node: GraphNode, sha256: str, source_name: str, derived_file: Path
) -> None:
    """Add the people, years and links found in one document's text.

    A document that cannot be read or fails part way is skipped from that point; what it
    had already added stays, as it always did.
    """
    try:
        doc_payload = json.loads(derived_file.read_text(encoding="utf-8"))
        segments = doc_payload.get("segments", [])
        for idx, seg in enumerate(segments):
            seg_text = seg.get("text", "")
            seg_loc = seg.get("locator", {"segment_index": idx})
            raw_names = re.findall(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", seg_text)
            mention_nodes: list[GraphNode] = []
            for rn in raw_names:
                pn = _clean_person_name(rn)
                if pn:
                    mention_node = work.node("candidate_mention", canonical_name=pn)
                    mention_nodes.append(mention_node)
                    snip_start = max(0, seg_text.find(rn) - 20)
                    snip_end = min(len(seg_text), snip_start + 100)
                    snippet = seg_text[snip_start:snip_end].strip()
                    work.edge(
                        mention_node.node_id,
                        obj_node.node_id,
                        "mentioned_in",
                        0.85,
                        "probable",
                        [
                            GraphCitation(
                                object_sha256=sha256,
                                source_name=source_name,
                                locator=seg_loc,
                                snippet=snippet,
                            )
                        ],
                    )
            seg_years = re.findall(
                r"(?:^|[\W_])(18\d\d|19\d\d|20\d\d)(?:$|[\W_])",
                seg_text,
            )
            for sy in seg_years:
                y_val = int(sy)
                d_node = work.node(
                    "date",
                    canonical_name=str(y_val),
                    properties={"year": y_val},
                )
                for mention_node in mention_nodes:
                    work.edge(
                        mention_node.node_id,
                        d_node.node_id,
                        "associated_with",
                        0.80,
                        "probable",
                        [
                            GraphCitation(
                                object_sha256=sha256,
                                source_name=source_name,
                                locator=seg_loc,
                                snippet=(
                                    f"Mentioned in {source_name} alongside year {y_val}"  # noqa: E501
                                ),
                            )
                        ],
                    )
            for i in range(len(mention_nodes)):
                for j in range(i + 1, len(mention_nodes)):
                    p1, p2 = mention_nodes[i], mention_nodes[j]
                    if p1.node_id != p2.node_id:
                        work.edge(
                            p1.node_id,
                            p2.node_id,
                            "co_occurs_with",
                            0.75,
                            "probable",
                            [
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
                        work.edge(
                            p2.node_id,
                            p1.node_id,
                            "co_occurs_with",
                            0.75,
                            "probable",
                            [
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
    except _WorkFileError:
        raise
    except Exception:
        pass


def _add_faces(work: _Work, layout: ArchivLayout) -> None:
    """Add the people found in photographs, and link them to what the photographs are dated."""
    f_db = face_index_path(layout)
    if not f_db.is_file():
        return
    try:
        with connect_face_index(f_db) as f_conn:
            clusters = f_conn.execute("SELECT cluster_id, label FROM face_clusters").fetchall()  # noqa: E501
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
                person_node = work.node("person", canonical_name=person_name)
                if conf:
                    _promote_candidate_edges(work, person_node)
                m_rows = f_conn.execute(
                    """
                    SELECT face_id, object_sha256, source_name, bbox_json, confidence
                    FROM faces WHERE cluster_id = ?
                    """,
                    (cid,),
                ).fetchall()
                for mr in m_rows:
                    img_sha = str(mr["object_sha256"])
                    src_name = str(mr["source_name"])
                    bbox = json.loads(str(mr["bbox_json"]))
                    det_conf = float(mr["confidence"])
                    img_node = work.node(
                        "image",
                        canonical_name=src_name,
                        properties={"sha256": img_sha},
                    )
                    work.edge(
                        person_node.node_id,
                        img_node.node_id,
                        "appears_in",
                        round(p_conf * det_conf, 3),
                        status,
                        [
                            GraphCitation(
                                object_sha256=img_sha,
                                source_name=src_name,
                                locator={"bbox": bbox},
                                snippet=(
                                    f"Detected face in photograph '{src_name}' (conf: {det_conf:.2f})"  # noqa: E501
                                ),
                            )
                        ],
                    )
                    for ie in work.edges_from(img_node.node_id, "associated_with"):
                        work.edge(
                            person_node.node_id,
                            ie.target_node_id,
                            "associated_with",
                            round(p_conf * 0.90, 3),
                            status,
                            ie.citations,
                        )
    except _WorkFileError:
        raise
    except Exception:
        pass


def rebuild_graph(home: Path | None = None) -> tuple[int, int]:
    """Rebuild the SQLite entity graph from canonical objects, derived segments, and faces.

    The graph is built inside a working file, one document at a time, so the memory needed
    follows the largest single document and not the size of the collection.
    """
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    final_path = graph_index_path(layout)
    # Taken before anything is read: if a document changes while the graph is being built,
    # the next look sees a different fingerprint and rebuilds again.
    inputs_fingerprint = graph_inputs_fingerprint(layout)
    tmp_path = layout.temporary / f"graph-{uuid4().hex[:12]}.sqlite3.tmp"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    try:
        with connect_graph_index(tmp_path) as g_conn:
            # The working file is thrown away if anything goes wrong and is replaced in one
            # step at the end, so it needs neither a journal nor a flush to disk.
            g_conn.execute("PRAGMA journal_mode = OFF")
            g_conn.execute("PRAGMA synchronous = OFF")
            g_conn.execute(f"PRAGMA cache_size = -{_CACHE_KIB}")
            # Keeping four lookup indexes up to date while millions of rows go in costs more
            # than building them once afterwards; they are needed again before the faces.
            for index in _LOOKUP_INDEXES:
                g_conn.execute(f"DROP INDEX IF EXISTS {index}")
            work = _Work(g_conn, now)
            _add_objects(work, layout)
            g_conn.executescript(GRAPH_SCHEMA)
            _add_faces(work, layout)
            work.flush()
            g_conn.execute(
                "INSERT OR REPLACE INTO graph_meta (key, value) VALUES (?, ?)",
                (FINGERPRINT_KEY, inputs_fingerprint),
            )
            g_conn.commit()
            stats = get_graph_stats(g_conn)
        os.replace(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return int(stats["total_nodes"]), int(stats["total_edges"])


def _add_objects(work: _Work, layout: ArchivLayout) -> None:
    if not layout.database.is_file():
        return
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
        entity_type: NodeType = "image" if is_image else "document"
        obj_node = work.node(
            entity_type,
            canonical_name=source_name,
            properties={"sha256": sha256, "media_type": media_type},
        )
        year_match = re.search(r"(?:^|[\W_])(18\d\d|19\d\d|20\d\d)(?:$|[\W_])", source_name)  # noqa: E501
        obj_year: int | None = int(year_match.group(1)) if year_match else None
        if obj_year:
            date_node = work.node(
                "date", canonical_name=str(obj_year), properties={"year": obj_year}
            )
            work.edge(
                obj_node.node_id,
                date_node.node_id,
                "associated_with",
                0.90,
                "probable",
                [
                    GraphCitation(
                        object_sha256=sha256,
                        source_name=source_name,
                        locator={"filename": source_name},
                        snippet=f"Year {obj_year} found in filename '{source_name}'",
                    )
                ],
            )
        derived_file = layout.derived_root(sha256) / "normalized" / "document.json"
        if derived_file.is_file():
            _add_document_text(work, obj_node, sha256, source_name, derived_file)
        work.flush()
