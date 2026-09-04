"""Durable processing queue management for deep-tier jobs.

Enables asynchronous, resumable deep-tier operations (visual OCR,
transcription, embeddings) as specified in PR 108 Milestone 1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from archiv.storage.database import ArchivDatabase


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_queue_depth(database: ArchivDatabase) -> dict[str, int]:
    """Return counts of queue jobs by state."""
    counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
    try:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) as count FROM processing_queue GROUP BY state"
            ).fetchall()
            for row in rows:
                state = str(row["state"])
                if state in counts:
                    counts[state] = int(row["count"])
    except Exception:
        pass
    return counts


def enqueue_job(
    database: ArchivDatabase,
    object_sha256: str,
    processor: str,
    *,
    processor_version: str = "1",
    state: str = "pending",
) -> None:
    """Enqueue a job into the durable processing queue."""
    now = _now_iso()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO processing_queue (
                object_sha256, processor, processor_version, state,
                attempts, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (object_sha256, processor, processor_version, state, now, now),
        )
        connection.commit()


def update_job(
    database: ArchivDatabase,
    object_sha256: str,
    processor: str,
    *,
    state: str,
    error: str | None = None,
) -> None:
    """Update job state and record attempt details."""
    now = _now_iso()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE processing_queue
            SET state = ?, error = ?, updated_at = ?, attempts = attempts + 1
            WHERE object_sha256 = ? AND processor = ?
            """,
            (state, error, now, object_sha256, processor),
        )
        connection.commit()


def fetch_pending_jobs(
    database: ArchivDatabase,
    *,
    limit: int | None = None,
    processor: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch pending jobs ordered by arrival time."""
    query = "SELECT * FROM processing_queue WHERE state = 'pending'"
    params: list[object] = []
    if processor is not None:
        query += " AND processor = ?"
        params.append(processor)
    query += " ORDER BY created_at"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with database.connect() as connection:
        rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]
