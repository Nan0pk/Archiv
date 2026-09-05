"""Durable storage and lifecycle management for faces, clusters, and confirmations."""

from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from archiv.faces.contracts import FaceCluster, FaceDetection
from archiv.storage.layout import ArchivLayout

FACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS faces (
    face_id TEXT PRIMARY KEY,
    object_sha256 TEXT NOT NULL,
    source_name TEXT NOT NULL,
    bbox_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    embedding BLOB NOT NULL,
    cluster_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faces_object ON faces(object_sha256);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON faces(cluster_id);

CREATE TABLE IF NOT EXISTS face_clusters (
    cluster_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    member_count INTEGER NOT NULL,
    centroid BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS confirmations (
    cluster_id TEXT PRIMARY KEY,
    confirmed_name TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);
"""


def face_index_path(layout: ArchivLayout) -> Path:
    """Return path to faces index SQLite database."""
    return layout.indexes / "faces.sqlite3"


@contextmanager
def connect_face_index(path: Path) -> Generator[sqlite3.Connection]:
    """Open one short-lived connection to the face database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(FACES_SCHEMA)
        yield connection
    finally:
        connection.close()


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dim: int = 64) -> list[float]:
    return list(struct.unpack(f"{dim}f", blob))


def save_detection(
    conn: sqlite3.Connection,
    detection: FaceDetection,
    cluster_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO faces (
            face_id, object_sha256, source_name, bbox_json,
            confidence, embedding, cluster_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            detection.face_id,
            detection.object_sha256,
            detection.source_name,
            json.dumps(detection.bbox),
            detection.confidence,
            pack_vector(detection.embedding),
            cluster_id,
            datetime.now(UTC).isoformat(),
        ),
    )


def list_clusters(conn: sqlite3.Connection) -> list[FaceCluster]:
    cursor = conn.execute(
        """
        SELECT cluster_id, label, member_count, centroid, created_at, updated_at
        FROM face_clusters
        ORDER BY member_count DESC, created_at ASC
        """
    )
    results: list[FaceCluster] = []
    for row in cursor:
        results.append(
            FaceCluster(
                cluster_id=str(row["cluster_id"]),
                label=str(row["label"]),
                member_count=int(row["member_count"]),
                centroid=unpack_vector(bytes(row["centroid"]), 64),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
        )
    return results


def forget_face_data(layout: ArchivLayout, cluster_id: str | None = None) -> int:
    """First-class erasure: delete biometric data leaving original images untouched."""
    path = face_index_path(layout)
    if not path.is_file():
        return 0

    with connect_face_index(path) as conn:
        if cluster_id is None:
            # Erase all biometric data
            deleted = conn.execute("SELECT COUNT(*) as c FROM faces").fetchone()
            count = int(deleted["c"]) if deleted else 0
            conn.execute("DELETE FROM faces")
            conn.execute("DELETE FROM face_clusters")
            conn.execute("DELETE FROM confirmations")
            conn.commit()
            return count

        # Erase specific cluster
        deleted = conn.execute(
            "SELECT COUNT(*) as c FROM faces WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
        count = int(deleted["c"]) if deleted else 0
        conn.execute("DELETE FROM faces WHERE cluster_id = ?", (cluster_id,))
        conn.execute("DELETE FROM face_clusters WHERE cluster_id = ?", (cluster_id,))
        conn.execute("DELETE FROM confirmations WHERE cluster_id = ?", (cluster_id,))
        conn.commit()
        return count


def confirm_cluster_name(layout: ArchivLayout, cluster_id: str, name: str) -> None:
    """Record a user confirmation for a face cluster."""
    path = face_index_path(layout)
    with connect_face_index(path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO confirmations (cluster_id, confirmed_name, confirmed_at)
            VALUES (?, ?, ?)
            """,
            (cluster_id, name.strip(), datetime.now(UTC).isoformat()),
        )
        conn.commit()


def revoke_cluster_confirmation(layout: ArchivLayout, cluster_id: str) -> bool:
    """Revoke a previously confirmed name for a cluster."""
    path = face_index_path(layout)
    if not path.is_file():
        return False
    with connect_face_index(path) as conn:
        cursor = conn.execute("DELETE FROM confirmations WHERE cluster_id = ?", (cluster_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_confirmation(conn: sqlite3.Connection, cluster_id: str) -> tuple[str, str] | None:
    """Return (confirmed_name, confirmed_at) if cluster is confirmed."""
    row = conn.execute(
        "SELECT confirmed_name, confirmed_at FROM confirmations WHERE cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if row:
        return str(row["confirmed_name"]), str(row["confirmed_at"])
    return None
