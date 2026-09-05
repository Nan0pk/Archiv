"""Immutable ingestion and rebuildable derived-data service."""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from archiv.contracts import (
    IngestionResult,
    IngestionStatus,
    NormalizedDocument,
    ProcessingEvidence,
)
from archiv.hashing import copy_and_hash, sha256_bytes, sha256_file
from archiv.ingestion.derive import (
    derive,
    derive_artifacts,
    reuse_derived_artifacts,
)
from archiv.ingestion.ledger import (
    now_iso,
    record_processing_batch,
)
from archiv.ingestion.limits import (
    MAX_EXPANDED_BYTES,
    MAX_RECURSION_DEPTH,
    LimitExceededError,
    check_input,
)
from archiv.ingestion.normalizers import (
    UnsupportedFormatError,
    media_type_for,
    normalize,
    suffix_for,
)
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout
from archiv.storage.queue import enqueue_job


def _store_original(source: Path, target: Path, digest: str) -> bool:
    if target.exists():
        if sha256_file(target) != digest:
            raise RuntimeError("content-address collision or corrupted original")
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    copied_digest = copy_and_hash(source, temporary)
    if copied_digest != digest:
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


def _record_ingestion_failure(
    database: ArchivDatabase, *, source: Path, digest: str | None, error: Exception
) -> None:
    """Durably record a file that was rejected before it ever reached storage.

    No ``objects``/``ingestions`` row exists for a rejected file by design (see
    ``_migration_1_to_2``), so this is the only place the attempt is remembered.
    """

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO ingestion_failures (
                failure_id, source_path, source_name, object_sha256, error, attempted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                str(source),
                source.name,
                digest,
                f"{type(error).__name__}: {error}",
                now_iso(),
            ),
        )
        connection.commit()


@dataclass(frozen=True)
class PreparedCandidate:
    source: Path
    source_name: str
    digest: str
    media_type: str
    source_extension: str
    target: Path
    duplicate: bool
    evidence: list[ProcessingEvidence]
    pending_records: list[tuple[ProcessingEvidence, str, str]]
    queue_jobs: list[tuple[str, str, str, str]]
    normalized: NormalizedDocument


def prepare_candidate(
    source: Path,
    layout: ArchivLayout,
    *,
    rebuild_derived: bool = False,
) -> PreparedCandidate:
    """Validate, hash, copy original, and derive artifacts without database writes."""

    source = source.expanduser()
    check_input(source)
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("source must be a regular file")

    digest = sha256_file(source)
    source_name = source.name
    media_type = media_type_for(source_name)
    source_extension = suffix_for(source_name)
    normalized = normalize(source, digest, source_name=source_name)

    target = layout.original_path(digest)
    lock_prefix = sha256_bytes(str(layout.root).encode("utf-8"))[:12]
    lock_path = Path(tempfile.gettempdir()) / f"archiv-lock-{lock_prefix}-{digest}.lock"
    with open(lock_path, "a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            duplicate = _store_original(source, target, digest)

            if duplicate and not rebuild_derived:
                evidence, pending_records = reuse_derived_artifacts(digest, layout)
                queue_jobs: list[tuple[str, str, str, str]] = []
                derived_doc = normalized
            else:
                derived = derive_artifacts(
                    target,
                    digest,
                    source_name,
                    layout,
                    replace=True,
                    normalized=normalized,
                )
                evidence = derived.evidence
                pending_records = derived.pending_records
                queue_jobs = derived.queue_jobs
                derived_doc = derived.normalized
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    if sha256_file(source) != digest:
        raise RuntimeError("source hash changed during ingestion")
    if sha256_file(target) != digest:
        raise RuntimeError("stored original failed post-processing integrity check")

    return PreparedCandidate(
        source=source,
        source_name=source_name,
        digest=digest,
        media_type=media_type,
        source_extension=source_extension,
        target=target,
        duplicate=duplicate,
        evidence=evidence,
        pending_records=pending_records,
        queue_jobs=queue_jobs,
        normalized=derived_doc,
    )


def commit_candidate(
    database: ArchivDatabase,
    layout: ArchivLayout,
    prepared: PreparedCandidate,
    *,
    rebuild_derived: bool = False,
    _depth: int = 0,
    _aggregate_bytes: list[int] | None = None,
) -> IngestionResult:
    """Durably record a prepared candidate into SQLite and handle archive containment."""

    aggregate_bytes = _aggregate_bytes if _aggregate_bytes is not None else [0]
    target = prepared.target
    digest = prepared.digest
    normalized = prepared.normalized

    with database.connect() as connection:
        existing_ingestion = connection.execute(
            "SELECT 1 FROM ingestions WHERE object_sha256 = ? AND status = 'succeeded'",
            (digest,),
        ).fetchone()
    duplicate = prepared.duplicate or (existing_ingestion is not None)

    ingestion_id = uuid4().hex
    _insert_pending_ingestion(
        database,
        ingestion_id=ingestion_id,
        digest=digest,
        source=prepared.source,
        source_name=prepared.source_name,
        media_type=prepared.media_type,
        source_extension=prepared.source_extension,
        target=target,
        duplicate=duplicate,
    )

    try:
        for q_digest, q_processor, q_version, q_state in prepared.queue_jobs:
            enqueue_job(
                database,
                q_digest,
                q_processor,
                processor_version=q_version,
                state=q_state,
            )
        record_processing_batch(database, digest, prepared.pending_records)

        if (
            normalized.kind == "archive"
            and not normalized.metadata.get("archive_locked")
            and (not duplicate or rebuild_derived)
        ):
            total_uncompressed = int(str(normalized.metadata.get("total_uncompressed_bytes") or 0))
            aggregate_bytes[0] += total_uncompressed
            if aggregate_bytes[0] > MAX_EXPANDED_BYTES:
                raise LimitExceededError(
                    f"archive expanded bytes limit exceeded: "
                    f"{aggregate_bytes[0]} > {MAX_EXPANDED_BYTES}"
                )

            temp_dir = layout.temporary / f"extract-{digest[:16]}-{uuid4().hex}"
            temp_dir.mkdir(parents=True, exist_ok=True)
            try:
                archive_format = normalized.metadata.get("format")
                if archive_format == "zip":
                    with ZipFile(target) as archive:
                        for member in archive.infolist():
                            if member.is_dir():
                                continue
                            archive.extract(member, path=temp_dir)
                            extracted_path = temp_dir / member.filename
                            if not extracted_path.resolve().is_relative_to(temp_dir.resolve()):
                                raise LimitExceededError(f"unsafe member path: {member.filename!r}")
                            date_iso = None
                            with contextlib.suppress(Exception):
                                date_iso = (
                                    f"{member.date_time[0]:04d}-{member.date_time[1]:02d}-"
                                    f"{member.date_time[2]:02d}T{member.date_time[3]:02d}:"
                                    f"{member.date_time[4]:02d}:{member.date_time[5]:02d}Z"
                                )
                            compression = "deflate" if member.compress_type == 8 else "stored"
                            try:
                                child_res = ingest_file(
                                    extracted_path,
                                    home=layout.root,
                                    rebuild_derived=rebuild_derived,
                                    _depth=_depth + 1,
                                    _aggregate_bytes=aggregate_bytes,
                                )
                            except UnsupportedFormatError:
                                continue

                            with database.connect() as connection:
                                connection.execute(
                                    """
                                    INSERT OR REPLACE INTO containment (
                                        parent_sha256, child_sha256, internal_path,
                                        member_modified_at, compression, depth
                                    ) VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        digest,
                                        child_res.object_sha256,
                                        member.filename,
                                        date_iso,
                                        compression,
                                        _depth + 1,
                                    ),
                                )
                                connection.commit()
                elif archive_format == "tar":
                    with tarfile.open(target, "r:*") as archive:
                        for member in archive:
                            if not member.isfile():
                                continue
                            archive.extract(member, path=temp_dir, filter="data")
                            extracted_path = temp_dir / member.name
                            if not extracted_path.resolve().is_relative_to(temp_dir.resolve()):
                                raise LimitExceededError(f"unsafe member path: {member.name!r}")
                            date_iso = None
                            with contextlib.suppress(Exception):
                                date_iso = datetime.fromtimestamp(member.mtime, UTC).strftime(
                                    "%Y-%m-%dT%H:%M:%SZ"
                                )
                            compression = "tar"
                            try:
                                child_res = ingest_file(
                                    extracted_path,
                                    home=layout.root,
                                    rebuild_derived=rebuild_derived,
                                    _depth=_depth + 1,
                                    _aggregate_bytes=aggregate_bytes,
                                )
                            except UnsupportedFormatError:
                                continue

                            with database.connect() as connection:
                                connection.execute(
                                    """
                                    INSERT OR REPLACE INTO containment (
                                        parent_sha256, child_sha256, internal_path,
                                        member_modified_at, compression, depth
                                    ) VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        digest,
                                        child_res.object_sha256,
                                        member.name,
                                        date_iso,
                                        compression,
                                        _depth + 1,
                                    ),
                                )
                                connection.commit()
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as error:
        _finish_ingestion(database, ingestion_id, error=error)
        raise

    _finish_ingestion(database, ingestion_id)
    return IngestionResult(
        ingestion_id=ingestion_id,
        status=IngestionStatus.SUCCEEDED,
        object_sha256=digest,
        duplicate=duplicate,
        media_type=prepared.media_type,
        original_path=str(target),
        derived_root=str(layout.derived_root(digest)),
        source_hash_unchanged=True,
        processing=prepared.evidence,
    )


def ingest_file(
    source: Path,
    *,
    home: Path | None = None,
    rebuild_derived: bool = False,
    _depth: int = 0,
    _aggregate_bytes: list[int] | None = None,
) -> IngestionResult:
    """Validate, content-address, record, and derive one local file."""

    if _depth > MAX_RECURSION_DEPTH:
        raise LimitExceededError(
            f"archive recursion depth limit exceeded: {_depth} > {MAX_RECURSION_DEPTH}"
        )
    aggregate_bytes = _aggregate_bytes if _aggregate_bytes is not None else [0]

    source = source.expanduser()
    layout = ArchivLayout.resolve(home)
    home_already_exists = layout.root.exists()

    digest: str | None = None
    try:
        prepared = prepare_candidate(source, layout, rebuild_derived=rebuild_derived)
        digest = prepared.digest
    except Exception as error:
        if home_already_exists:
            layout.ensure()
            database = ArchivDatabase(layout.database)
            database.initialize()
            _record_ingestion_failure(database, source=source, digest=digest, error=error)
        raise

    layout.ensure()
    database = ArchivDatabase(layout.database)
    database.initialize()

    return commit_candidate(
        database,
        layout,
        prepared,
        rebuild_derived=rebuild_derived,
        _depth=_depth,
        _aggregate_bytes=aggregate_bytes,
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


record_ingestion_failure = _record_ingestion_failure

__all__ = [
    "PreparedCandidate",
    "commit_candidate",
    "ingest_file",
    "prepare_candidate",
    "rebuild_derived",
    "record_ingestion_failure",
]
