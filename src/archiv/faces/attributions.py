"""Cluster attribution for the opt-in face workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from archiv.faces.config import check_faces_opt_in
from archiv.faces.contracts import ClusterAttribution
from archiv.faces.storage import connect_face_index, face_index_path, get_confirmation
from archiv.storage.layout import ArchivLayout


def attribute_cluster(
    layout: ArchivLayout,
    cluster_id: str,
    label: str | None = None,
) -> ClusterAttribution | None:
    """Return cluster state without automatically guessing a person's name."""
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return None

    with connect_face_index(db_path) as conn:
        row = conn.execute(
            "SELECT cluster_id, label, member_count FROM face_clusters WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if not row:
            return None

        cluster_label = label or str(row["label"])
        member_count = int(row["member_count"])
        confirmation = get_confirmation(conn, cluster_id)

    if confirmation:
        status: Literal["unconfirmed", "confirmed"] = "confirmed"
        confirmed_name, confirmed_at = confirmation
    else:
        status = "unconfirmed"
        confirmed_name, confirmed_at = None, None

    return ClusterAttribution(
        cluster_id=cluster_id,
        label=cluster_label,
        member_count=member_count,
        status=status,
        confirmed_name=confirmed_name,
        confirmed_at=confirmed_at,
        candidates=[],
    )


def attribute_all_clusters(home: Path | None = None) -> list[ClusterAttribution]:
    """Retrieve attribution state for all face clusters."""
    check_faces_opt_in(home)
    layout = ArchivLayout.resolve(home)
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return []

    with connect_face_index(db_path) as conn:
        rows = conn.execute(
            "SELECT cluster_id, label FROM face_clusters ORDER BY member_count DESC, created_at ASC"
        ).fetchall()

    attributions: list[ClusterAttribution] = []
    for row in rows:
        attr = attribute_cluster(layout, str(row["cluster_id"]), str(row["label"]))
        if attr is not None:
            attributions.append(attr)
    return attributions


def find_cluster_by_target(
    layout: ArchivLayout,
    target: str,
) -> tuple[str, str] | None:
    """Find a cluster by identifier, generated label, or explicitly confirmed name."""
    db_path = face_index_path(layout)
    if not db_path.is_file():
        return None

    target_str = target.strip()
    with connect_face_index(db_path) as conn:
        row = conn.execute(
            "SELECT cluster_id, label FROM face_clusters WHERE cluster_id = ?",
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

        row = conn.execute(
            "SELECT cluster_id, label FROM face_clusters WHERE LOWER(label) = LOWER(?)",
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

        row = conn.execute(
            """
            SELECT c.cluster_id, fc.label
            FROM confirmations c
            JOIN face_clusters fc ON c.cluster_id = fc.cluster_id
            WHERE LOWER(c.confirmed_name) = LOWER(?)
            """,
            (target_str,),
        ).fetchone()
        if row:
            return str(row["cluster_id"]), str(row["label"])

    return None
