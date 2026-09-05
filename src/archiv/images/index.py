"""Atomic construction and connection management for rebuildable image embedding index."""

from __future__ import annotations

import os
import sqlite3
import struct
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from archiv.images.contracts import ImageIndexBuildResult
from archiv.images.embedder import ImageEmbedder, get_default_image_embedder
from archiv.storage.layout import ArchivLayout

IMAGE_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_embeddings (
    object_sha256 TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_image_embeddings_model ON image_embeddings(model_name);
"""


def image_index_path(layout: ArchivLayout) -> Path:
    """Return the replaceable image embeddings database path."""
    return layout.indexes / "images.sqlite3"


@contextmanager
def connect_image_index(path: Path) -> Generator[sqlite3.Connection]:
    """Open one short-lived image-index connection."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def pack_embedding(vec: list[float]) -> bytes:
    """Pack float vector into binary IEEE 754 float32 bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes, dimensions: int) -> list[float]:
    """Unpack float32 binary blob into python float list."""
    return list(struct.unpack(f"{dimensions}f", blob))


def rebuild_image_index(
    *,
    home: Path | None = None,
    embedder: ImageEmbedder | None = None,
) -> ImageIndexBuildResult:
    """Atomically rebuild the image embedding index from canonical objects."""
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    active_embedder = embedder or get_default_image_embedder()

    start_time = time.monotonic()
    temporary = layout.indexes / f".images-{uuid4().hex}.sqlite3"
    final = image_index_path(layout)
    object_count = 0

    try:
        with connect_image_index(temporary) as index_conn:
            index_conn.executescript(IMAGE_INDEX_SCHEMA)

            if layout.database.is_file():
                with sqlite3.connect(layout.database) as source_conn:
                    source_conn.row_factory = sqlite3.Row
                    cursor = source_conn.execute(
                        """
                        SELECT o.sha256, o.media_type, o.storage_path,
                               COALESCE(i.source_name, 'unknown') as source_name
                        FROM objects o
                        LEFT JOIN ingestions i ON o.sha256 = i.object_sha256
                        WHERE o.media_type LIKE 'image/%'
                        GROUP BY o.sha256
                        ORDER BY o.created_at ASC
                        """
                    )
                    rows = cursor.fetchall()

                    for row in rows:
                        digest = str(row["sha256"])
                        media_type = str(row["media_type"])
                        source_name = str(row["source_name"])
                        original_path = layout.original_path(digest)

                        if not original_path.is_file():
                            continue

                        try:
                            with Image.open(original_path) as img:
                                width, height = img.width, img.height
                            vec = active_embedder.embed_image(original_path)
                            packed = pack_embedding(vec)

                            index_conn.execute(
                                """
                                INSERT OR REPLACE INTO image_embeddings (
                                    object_sha256, media_type, source_name,
                                    width, height, dimensions, embedding,
                                    model_name, created_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    digest,
                                    media_type,
                                    source_name,
                                    width,
                                    height,
                                    active_embedder.dimensions,
                                    packed,
                                    active_embedder.model_name,
                                    datetime.now(UTC).isoformat(),
                                ),
                            )
                            object_count += 1
                        except Exception:
                            # Skip malformed images without halting index construction
                            continue

            index_conn.commit()

        os.replace(temporary, final)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)

    elapsed = time.monotonic() - start_time
    file_size = final.stat().st_size if final.exists() else 0

    return ImageIndexBuildResult(
        object_count=object_count,
        index_size_bytes=file_size,
        elapsed_seconds=elapsed,
        model_name=active_embedder.model_name,
        index_path=str(final),
    )
