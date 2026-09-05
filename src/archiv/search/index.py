"""Atomic construction of the rebuildable SQLite FTS5 index."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from archiv.contracts import NormalizedDocument, SearchIndexBuild
from archiv.hashing import sha256_bytes, sha256_file
from archiv.search.schema import SCHEMA, connect_index
from archiv.storage.layout import ArchivLayout


class SearchIndexIntegrityError(RuntimeError):
    """Durable source or normalized evidence cannot support an index row."""


def search_index_path(layout: ArchivLayout) -> Path:
    """Return the replaceable FTS database path."""

    return layout.indexes / "search.sqlite3"


def segment_id_for(
    digest: str,
    segment_index: int,
    locator_json: str,
    text_sha256: str,
) -> str:
    """Build a deterministic segment identifier from immutable evidence."""

    payload = f"{digest}\0{segment_index}\0{locator_json}\0{text_sha256}".encode()
    return sha256_bytes(payload)


def rebuild_search_index(*, home: Path | None = None) -> SearchIndexBuild:
    """Atomically rebuild FTS5 from canonical originals and normalized documents."""

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    temporary = layout.indexes / f".search-{uuid4().hex}.sqlite3"
    final = search_index_path(layout)
    object_count = 0
    segment_count = 0

    try:
        with connect_index(temporary) as index_connection:
            try:
                index_connection.executescript(SCHEMA)
            except sqlite3.OperationalError as error:
                raise RuntimeError(f"SQLite FTS5 is unavailable: {error}") from error

            with sqlite3.connect(layout.database) as source_connection:
                source_connection.row_factory = sqlite3.Row
                rows = source_connection.execute(
                    """
                    SELECT o.sha256
                    FROM objects AS o
                    WHERE EXISTS (
                        SELECT 1 FROM ingestions AS i
                        WHERE i.object_sha256 = o.sha256 AND i.status = 'succeeded'
                    )
                    ORDER BY o.sha256
                    """
                ).fetchall()

            for row in rows:
                digest = str(row["sha256"])
                original = layout.original_path(digest)
                normalized_path = layout.derived_root(digest) / "normalized" / "document.json"
                if not original.is_file():
                    raise SearchIndexIntegrityError(f"canonical original missing: {digest}")
                if not normalized_path.is_file():
                    raise SearchIndexIntegrityError(f"normalized document missing: {digest}")

                normalized_bytes = normalized_path.read_bytes()
                normalized_sha256 = sha256_bytes(normalized_bytes)
                document = NormalizedDocument.model_validate_json(normalized_bytes)
                if document.object_sha256 != digest:
                    raise SearchIndexIntegrityError(f"normalized object digest mismatch: {digest}")

                object_count += 1
                for segment_index, segment in enumerate(document.segments):
                    if not segment.text.strip():
                        continue
                    locator_json = json.dumps(
                        segment.locator,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    text_sha256 = sha256_bytes(segment.text.encode("utf-8"))
                    segment_id = segment_id_for(
                        digest,
                        segment_index,
                        locator_json,
                        text_sha256,
                    )
                    index_connection.execute(
                        """
                        INSERT INTO segments (
                            segment_id, segment_index, object_sha256, source_name,
                            media_type, kind, locator_json, text, text_sha256,
                            normalized_path, normalized_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            segment_id,
                            segment_index,
                            digest,
                            document.source_name,
                            document.media_type,
                            document.kind,
                            locator_json,
                            segment.text,
                            text_sha256,
                            str(normalized_path),
                            normalized_sha256,
                        ),
                    )
                    segment_count += 1

            index_connection.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
            index_connection.executemany(
                "INSERT INTO index_metadata(key, value) VALUES (?, ?)",
                [
                    ("schema_version", "1"),
                    ("object_count", str(object_count)),
                    ("segment_count", str(segment_count)),
                ],
            )
            index_connection.commit()
        os.replace(temporary, final)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return SearchIndexBuild(
        index_path=str(final),
        index_sha256=sha256_file(final),
        object_count=object_count,
        segment_count=segment_count,
    )


def update_search_index(
    new_digests: Iterable[str] | None = None,
    *,
    home: Path | None = None,
    full: bool = False,
) -> SearchIndexBuild:
    """Incrementally update or rebuild FTS5 search index with new or updated objects."""

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    final = search_index_path(layout)

    if full or not final.is_file():
        return rebuild_search_index(home=home)

    try:
        with connect_index(final) as index_connection:
            try:
                tables = {
                    row[0]
                    for row in index_connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if not {"segments", "segments_fts", "index_metadata"}.issubset(tables):
                    return rebuild_search_index(home=home)
            except sqlite3.Error:
                return rebuild_search_index(home=home)

            if new_digests is not None:
                candidate_digests = sorted(set(str(d) for d in new_digests))
            else:
                with sqlite3.connect(layout.database) as source_connection:
                    source_connection.row_factory = sqlite3.Row
                    rows = source_connection.execute(
                        """
                        SELECT o.sha256
                        FROM objects AS o
                        WHERE EXISTS (
                            SELECT 1 FROM ingestions AS i
                            WHERE i.object_sha256 = o.sha256 AND i.status = 'succeeded'
                        )
                        ORDER BY o.sha256
                        """
                    ).fetchall()
                candidate_digests = [str(row["sha256"]) for row in rows]

            if not candidate_digests:
                row = index_connection.execute(
                    "SELECT "
                    "(SELECT COUNT(DISTINCT object_sha256) FROM segments), "
                    "(SELECT COUNT(*) FROM segments)"
                ).fetchone()
                return SearchIndexBuild(
                    index_path=str(final),
                    index_sha256=sha256_file(final),
                    object_count=int(row[0]) if row else 0,
                    segment_count=int(row[1]) if row else 0,
                )

            for digest in candidate_digests:
                original = layout.original_path(digest)
                normalized_path = layout.derived_root(digest) / "normalized" / "document.json"
                if not original.is_file():
                    raise SearchIndexIntegrityError(f"canonical original missing: {digest}")
                if not normalized_path.is_file():
                    raise SearchIndexIntegrityError(f"normalized document missing: {digest}")

                normalized_bytes = normalized_path.read_bytes()
                normalized_sha256 = sha256_bytes(normalized_bytes)
                document = NormalizedDocument.model_validate_json(normalized_bytes)
                if document.object_sha256 != digest:
                    raise SearchIndexIntegrityError(f"normalized object digest mismatch: {digest}")

                existing_rows = index_connection.execute(
                    "SELECT rowid, text FROM segments WHERE object_sha256 = ?",
                    (digest,),
                ).fetchall()
                for old_rowid, old_text in existing_rows:
                    index_connection.execute(
                        "INSERT INTO segments_fts(segments_fts, rowid, text) "
                        "VALUES ('delete', ?, ?)",
                        (old_rowid, old_text),
                    )
                if existing_rows:
                    index_connection.execute(
                        "DELETE FROM segments WHERE object_sha256 = ?",
                        (digest,),
                    )

                for segment_index, segment in enumerate(document.segments):
                    if not segment.text.strip():
                        continue
                    locator_json = json.dumps(
                        segment.locator,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    text_sha256 = sha256_bytes(segment.text.encode("utf-8"))
                    segment_id = segment_id_for(
                        digest,
                        segment_index,
                        locator_json,
                        text_sha256,
                    )
                    cursor = index_connection.execute(
                        """
                        INSERT INTO segments (
                            segment_id, segment_index, object_sha256, source_name,
                            media_type, kind, locator_json, text, text_sha256,
                            normalized_path, normalized_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            segment_id,
                            segment_index,
                            digest,
                            document.source_name,
                            document.media_type,
                            document.kind,
                            locator_json,
                            segment.text,
                            text_sha256,
                            str(normalized_path),
                            normalized_sha256,
                        ),
                    )
                    index_connection.execute(
                        "INSERT INTO segments_fts(rowid, text) VALUES (?, ?)",
                        (cursor.lastrowid, segment.text),
                    )

            counts_row = index_connection.execute(
                "SELECT "
                "(SELECT COUNT(DISTINCT object_sha256) FROM segments), "
                "(SELECT COUNT(*) FROM segments)"
            ).fetchone()
            object_count = int(counts_row[0]) if counts_row else 0
            segment_count = int(counts_row[1]) if counts_row else 0

            index_connection.execute(
                "INSERT INTO index_metadata(key, value) VALUES ('object_count', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(object_count),),
            )
            index_connection.execute(
                "INSERT INTO index_metadata(key, value) VALUES ('segment_count', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(segment_count),),
            )
            index_connection.commit()

        return SearchIndexBuild(
            index_path=str(final),
            index_sha256=sha256_file(final),
            object_count=object_count,
            segment_count=segment_count,
        )
    except sqlite3.OperationalError:
        return rebuild_search_index(home=home)
