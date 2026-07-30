"""Processing and source-name ledger helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from archiv.contracts import ProcessingEvidence
from archiv.storage.database import ArchivDatabase


def now_iso() -> str:
    """Return a timezone-aware timestamp for durable ledger rows."""

    return datetime.now(UTC).isoformat()


def record_processing(
    database: ArchivDatabase,
    digest: str,
    item: ProcessingEvidence,
    *,
    started_at: str,
    finished_at: str,
) -> None:
    """Append one processor outcome to the SQLite ledger."""

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO processing_runs (
                processing_id, object_sha256, processor, processor_version,
                parameters_json, status, output_kind, output_path,
                output_sha256, error, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                digest,
                item.processor,
                item.processor_version,
                "{}",
                item.status,
                item.output_kind,
                item.output_path,
                item.output_sha256,
                item.error,
                started_at,
                finished_at,
            ),
        )
        connection.commit()


def canonical_source_name(
    database: ArchivDatabase,
    digest: str,
    fallback: str,
) -> str:
    """Return the first successful source name for stable duplicate derivation."""

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT source_name FROM ingestions
            WHERE object_sha256 = ? AND status = 'succeeded'
            ORDER BY imported_at, ingestion_id
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
    return fallback if row is None else str(row["source_name"])
