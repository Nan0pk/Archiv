"""Durable storage and query primitives for the entity graph index."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from archiv.graph.contracts import (
    GraphCitation,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationType,
)
from archiv.storage.layout import ArchivLayout

GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_type_name ON nodes(entity_type, canonical_name);

CREATE TABLE IF NOT EXISTS edges (
    edge_id TEXT PRIMARY KEY,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation_type);
"""


def graph_index_path(layout: ArchivLayout) -> Path:
    """Return path to rebuildable graph.sqlite3 index."""
    return layout.indexes / "graph.sqlite3"


@contextmanager
def connect_graph_index(path: Path) -> Generator[sqlite3.Connection]:
    """Open one short-lived connection to the graph database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(GRAPH_SCHEMA)
        yield connection
    finally:
        connection.close()


def save_node(conn: sqlite3.Connection, node: GraphNode) -> None:
    """Insert or replace a node in the graph index."""
    conn.execute(
        """
        INSERT OR REPLACE INTO nodes (
            node_id, entity_type, canonical_name, aliases_json, properties_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            node.node_id,
            node.entity_type,
            node.canonical_name,
            json.dumps(node.aliases),
            json.dumps(node.properties),
            node.created_at,
        ),
    )


def save_edge(conn: sqlite3.Connection, edge: GraphEdge) -> None:
    """Insert or replace an edge in the graph index."""
    cits_payload = [c.model_dump(mode="json") for c in edge.citations]
    conn.execute(
        """
        INSERT OR REPLACE INTO edges (
            edge_id, source_node_id, target_node_id, relation_type,
            confidence, status, citations_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge.edge_id,
            edge.source_node_id,
            edge.target_node_id,
            edge.relation_type,
            edge.confidence,
            edge.status,
            json.dumps(cits_payload),
            edge.created_at,
        ),
    )


def _row_to_node(row: sqlite3.Row) -> GraphNode:
    return GraphNode(
        node_id=str(row["node_id"]),
        entity_type=row["entity_type"],  # pyright: ignore[reportArgumentType]
        canonical_name=str(row["canonical_name"]),
        aliases=json.loads(str(row["aliases_json"])),
        properties=json.loads(str(row["properties_json"])),
        created_at=str(row["created_at"]),
    )


def _row_to_edge(row: sqlite3.Row) -> GraphEdge:
    raw_cits = json.loads(str(row["citations_json"]))
    citations = [GraphCitation.model_validate(c) for c in raw_cits]
    return GraphEdge(
        edge_id=str(row["edge_id"]),
        source_node_id=str(row["source_node_id"]),
        target_node_id=str(row["target_node_id"]),
        relation_type=row["relation_type"],  # pyright: ignore[reportArgumentType]
        confidence=float(row["confidence"]),
        status=row["status"],  # pyright: ignore[reportArgumentType]
        citations=citations,
        created_at=str(row["created_at"]),
    )


def get_node(conn: sqlite3.Connection, node_id: str) -> GraphNode | None:
    row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
    return _row_to_node(row) if row else None


def find_node_by_name(
    conn: sqlite3.Connection,
    name: str,
    entity_type: NodeType | None = None,
) -> GraphNode | None:
    clean_name = name.strip()
    if entity_type:
        row = conn.execute(
            "SELECT * FROM nodes WHERE LOWER(canonical_name) = LOWER(?) AND entity_type = ?",
            (clean_name, entity_type),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM nodes WHERE LOWER(canonical_name) = LOWER(?)",
            (clean_name,),
        ).fetchone()
    return _row_to_node(row) if row else None


def get_out_edges(
    conn: sqlite3.Connection,
    source_id: str,
    relation_type: RelationType | None = None,
) -> list[GraphEdge]:
    if relation_type:
        rows = conn.execute(
            """
            SELECT * FROM edges
            WHERE source_node_id = ? AND relation_type = ?
            ORDER BY confidence DESC
            """,
            (source_id, relation_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM edges WHERE source_node_id = ? ORDER BY confidence DESC",
            (source_id,),
        ).fetchall()
    return [_row_to_edge(r) for r in rows]


def get_in_edges(
    conn: sqlite3.Connection,
    target_id: str,
    relation_type: RelationType | None = None,
) -> list[GraphEdge]:
    if relation_type:
        rows = conn.execute(
            """
            SELECT * FROM edges
            WHERE target_node_id = ? AND relation_type = ?
            ORDER BY confidence DESC
            """,
            (target_id, relation_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM edges WHERE target_node_id = ? ORDER BY confidence DESC",
            (target_id,),
        ).fetchall()
    return [_row_to_edge(r) for r in rows]


def get_graph_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Summary statistics of graph entities and evidence edges."""
    node_counts = {
        str(r["entity_type"]): int(r["c"])
        for r in conn.execute(
            "SELECT entity_type, COUNT(*) as c FROM nodes GROUP BY entity_type"
        ).fetchall()
    }
    edge_counts = {
        str(r["relation_type"]): int(r["c"])
        for r in conn.execute(
            "SELECT relation_type, COUNT(*) as c FROM edges GROUP BY relation_type"
        ).fetchall()
    }
    status_counts = {
        str(r["status"]): int(r["c"])
        for r in conn.execute("SELECT status, COUNT(*) as c FROM edges GROUP BY status").fetchall()
    }
    total_nodes = sum(node_counts.values())
    total_edges = sum(edge_counts.values())

    return {
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "nodes_by_type": node_counts,
        "edges_by_relation": edge_counts,
        "edges_by_status": status_counts,
    }
