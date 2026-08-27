"""Immutable ingestion and rebuildable derived-data service."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from archiv.contracts import IngestionResult, IngestionStatus, ProcessingEvidence
from archiv.hashing import sha256_file
from archiv.ingestion.derive import derive, reuse_derived
from archiv.ingestion.ledger import canonical_source_name, now_iso
from archiv.ingestion.limits import check_input
from archiv.ingestion.normalizers import media_type_for, normalize, suffix_for
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout


def _store_original(source: Path, target: Path, digest: str) -> bool:
    if target.exists():
        if sha256_file(target) != digest:
            raise RuntimeError("content-address collision or corrupted original")
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("copied original digest mismatch")
    os.chmod(temporary, 0o444)
    os.replace(temporary, target)
    return False


def _insert_pending_ingestion(
    database: ArchivDatabase,
    *,
    ingestion_id: str,
    digest: str,
    source: Path,
    source_name: str,
    media_type: str,
    source_extension: str,
    target: Path,
    duplicate: bool,
) -> None:
    imported_at = now_iso()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO objects (
                sha256, size, media_type, source_extension, storage_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                digest,
                source.stat().st_size,
                media_type,
                source_extension,
                str(target),
                imported_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingestions (
                ingestion_id, object_sha256, source_path, source_name,
                imported_at, duplicate, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL)
            """,
            (
                ingestion_id,
                digest,
                str(source),
                source_name,
                imported_at,
                int(duplicate),
            ),
        )
        connection.commit()


def _finish_ingestion(
    database: ArchivDatabase,
    ingestion_id: str,
    *,
    error: Exception | None = None,
) -> None:
    with database.connect() as connection:
        if error is None:
            connection.execute(
                "UPDATE ingestions SET status = 'succeeded' WHERE ingestion_id = ?",
                (ingestion_id,),
            )
        else:
            connection.execute(
                "UPDATE ingestions SET status = 'failed', error = ? WHERE ingestion_id = ?",
                (f"{type(error).__name__}: {error}", ingestion_id),
            )
        connection.commit()


def ingest_file(
    source: Path,
    *,
    home: Path | None = None,
    rebuild_derived: bool = False,
) -> IngestionResult:
    """Validate, content-address, record, and derive one local file."""

    source = source.expanduser()
    # Inspect the directory entry before resolution so symlink inputs cannot
    # silently cross the caller's intended trust boundary.
    check_input(source)
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be a regular file")

    digest = sha256_file(source)
    source_name = source.name
    media_type = media_type_for(source_name)
    source_extension = suffix_for(source_name)
    normalize(source, digest, source_name=source_name)

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    database = ArchivDatabase(layout.database)
    database.initialize()

    target = layout.original_path(digest)
    duplicate = _store_original(source, target, digest)
    ingestion_id = uuid4().hex
    _insert_pending_ingestion(
        database,
        ingestion_id=ingestion_id,
        digest=digest,
        source=source,
        source_name=source_name,
        media_type=media_type,
        source_extension=source_extension,
        target=target,
        duplicate=duplicate,
    )

    try:
        stable_name = canonical_source_name(database, digest, source_name)
        processing = (
            reuse_derived(digest, layout, database)
            if duplicate and not rebuild_derived
            else derive(
                target,
                digest,
                stable_name,
                layout,
                database,
                replace=True,
            )
        )
        if sha256_file(source) != digest:
            raise RuntimeError("source hash changed during ingestion")
        if sha256_file(target) != digest:
            raise RuntimeError("stored original failed post-processing integrity check")
    except Exception as error:
        _finish_ingestion(database, ingestion_id, error=error)
        raise

    _finish_ingestion(database, ingestion_id)
    return IngestionResult(
        ingestion_id=ingestion_id,
        status=IngestionStatus.SUCCEEDED,
        object_sha256=digest,
        duplicate=duplicate,
        media_type=media_type,
        original_path=str(target),
        derived_root=str(layout.derived_root(digest)),
        source_hash_unchanged=True,
        processing=processing,
    )


def rebuild_derived(
    digest: str,
    *,
    home: Path | None = None,
) -> list[ProcessingEvidence]:
    """Delete only derived outputs and deterministically rebuild them."""

    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("digest must be a lowercase SHA-256 value")

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    database = ArchivDatabase(layout.database)
    database.initialize()
    original = layout.original_path(digest)
    if not original.is_file():
        raise FileNotFoundError(f"unknown object: {digest}")

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
    if row is None:
        raise FileNotFoundError(f"object missing successful ingestion metadata: {digest}")

    original_hash = sha256_file(original)
    evidence = derive(
        original,
        digest,
        str(row["source_name"]),
        layout,
        database,
        replace=True,
    )
    if sha256_file(original) != original_hash:
        raise RuntimeError("immutable original changed during derived-data rebuild")
    return evidence
