"""Incremental face clustering and centroid maintenance."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from archiv.faces.config import check_faces_opt_in
from archiv.faces.contracts import FaceDetection
from archiv.faces.detector import detect_faces_in_image
from archiv.faces.storage import (
    connect_face_index,
    face_index_path,
    pack_vector,
    save_detection,
    unpack_vector,
)
from archiv.images.embedder import normalize_vector
from archiv.storage.layout import ArchivLayout


@dataclass
class _ClusterState:
    cluster_id: str
    label: str
    member_count: int
    centroid: list[float]


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    return max(-1.0, min(1.0, dot))


def scan_and_cluster_faces(
    home: Path | None = None,
    threshold: float | None = None,
) -> tuple[int, int]:
    """Scan all ingested image objects for faces and update clusters incrementally.

    Returns (faces_detected_count, total_clusters_count).
    """
    config = check_faces_opt_in(home)
    sim_threshold = threshold if threshold is not None else config.similarity_threshold

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    db_path = face_index_path(layout)

    # 1. Retrieve all image objects from canonical database
    images_to_scan: list[tuple[str, str, Path]] = []
    if layout.database.is_file():
        with sqlite3.connect(layout.database) as source_conn:
            source_conn.row_factory = sqlite3.Row
            rows = source_conn.execute(
                """
                SELECT o.sha256, o.storage_path, COALESCE(i.source_name, 'unknown') as source_name
                FROM objects o
                LEFT JOIN ingestions i ON o.sha256 = i.object_sha256
                WHERE o.media_type LIKE 'image/%'
                GROUP BY o.sha256
                ORDER BY o.created_at ASC
                """
            ).fetchall()
            for r in rows:
                digest = str(r["sha256"])
                orig = layout.original_path(digest)
                if orig.is_file():
                    images_to_scan.append((digest, str(r["source_name"]), orig))

    total_faces = 0

    with connect_face_index(db_path) as conn:
        # Load existing clusters
        cursor = conn.execute("SELECT cluster_id, label, member_count, centroid FROM face_clusters")
        clusters: list[_ClusterState] = []
        for r in cursor:
            clusters.append(
                _ClusterState(
                    cluster_id=str(r["cluster_id"]),
                    label=str(r["label"]),
                    member_count=int(r["member_count"]),
                    centroid=unpack_vector(bytes(r["centroid"]), 64),
                )
            )

        # Existing faces set to avoid re-detecting
        scanned_objects = {
            str(row["object_sha256"])
            for row in conn.execute("SELECT DISTINCT object_sha256 FROM faces").fetchall()
        }

        for digest, source_name, orig_path in images_to_scan:
            if digest in scanned_objects:
                continue

            detections = detect_faces_in_image(
                orig_path,
                object_sha256=digest,
                source_name=source_name,
                min_confidence=config.min_detection_confidence,
            )

            for det in detections:
                total_faces += 1
                assigned_cluster_id = _assign_or_create_cluster(conn, clusters, det, sim_threshold)
                save_detection(conn, det, cluster_id=assigned_cluster_id)

        conn.commit()
        total_clusters_count = len(clusters)

    return total_faces, total_clusters_count


def _assign_or_create_cluster(
    conn: sqlite3.Connection,
    clusters: list[_ClusterState],
    detection: FaceDetection,
    threshold: float,
) -> str:
    """Assign detection to nearest cluster exceeding threshold, or create a new cluster."""
    best_sim = -1.0
    best_cluster: _ClusterState | None = None

    for c in clusters:
        sim = _cosine_similarity(detection.embedding, c.centroid)
        if sim > best_sim:
            best_sim = sim
            best_cluster = c

    now = datetime.now(UTC).isoformat()

    if best_cluster is not None and best_sim >= threshold:
        # Update existing cluster centroid
        count = best_cluster.member_count
        old_centroid = best_cluster.centroid
        new_count = count + 1
        # Incremental moving average: (old * count + new) / (count + 1)
        new_centroid = normalize_vector(
            [
                (old_c * count + new_c) / float(new_count)
                for old_c, new_c in zip(old_centroid, detection.embedding, strict=False)
            ]
        )
        best_cluster.member_count = new_count
        best_cluster.centroid = new_centroid

        cid = best_cluster.cluster_id
        conn.execute(
            """
            UPDATE face_clusters
            SET member_count = ?, centroid = ?, updated_at = ?
            WHERE cluster_id = ?
            """,
            (new_count, pack_vector(new_centroid), now, cid),
        )
        return cid

    # Create new cluster: "Person N"
    new_label = f"Person {len(clusters) + 1}"
    cid = f"cluster_{uuid4().hex[:12]}"
    new_cluster = _ClusterState(
        cluster_id=cid,
        label=new_label,
        member_count=1,
        centroid=detection.embedding,
    )
    clusters.append(new_cluster)

    conn.execute(
        """
        INSERT INTO face_clusters (
            cluster_id, label, member_count, centroid, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (cid, new_label, 1, pack_vector(detection.embedding), now, now),
    )
    return cid
