"""A short fingerprint of everything the entity graph is built from.

The graph is rebuilt from the object store, each document's derived text and the face
index. If none of those changed, the stored graph is still right and need not be rebuilt.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator

from archiv.faces.storage import face_index_path
from archiv.storage.layout import ArchivLayout

# Raise this whenever the way the graph is built changes, so graphs built by older code are
# rebuilt once instead of being trusted.
GRAPH_BUILDER_VERSION = "1"


def graph_inputs_fingerprint(layout: ArchivLayout) -> str:
    """Hash the stored objects, their derived documents and the face index.

    Covers every object the builder reads (not only successfully ingested ones), each
    derived document's size and modification time (`rebuild-derived` rewrites that file
    under the same content hash), and the face index.
    """
    digest = hashlib.sha256()
    digest.update(f"graph-builder-{GRAPH_BUILDER_VERSION}\n".encode())
    if layout.database.is_file():
        connection = sqlite3.connect(f"file:{layout.database}?mode=ro", uri=True)
        try:
            objects = connection.execute(
                "SELECT sha256, media_type FROM objects ORDER BY sha256"
            ).fetchall()
            names = connection.execute(
                "SELECT DISTINCT object_sha256, source_name FROM ingestions "
                "ORDER BY object_sha256, source_name"
            ).fetchall()
        finally:
            connection.close()
        for sha256, media_type in objects:
            document = layout.derived_root(str(sha256)) / "normalized" / "document.json"
            try:
                stat = document.stat()
                state = f"{stat.st_size}:{stat.st_mtime_ns}"
            except OSError:
                state = "none"
            digest.update(f"object {sha256} {media_type} {state}\n".encode())
        for sha256, source_name in names:
            digest.update(f"name {sha256} {source_name}\n".encode())
    for line in _face_data_lines(layout):
        digest.update(line)
    return digest.hexdigest()


# What the graph reads from the face index, by content. A file's size and modification time
# are not enough: a name changed to one of the same length can leave both unchanged.
_FACE_QUERIES = (
    "SELECT face_id, object_sha256, source_name, bbox_json, confidence, cluster_id "
    "FROM faces ORDER BY face_id",
    "SELECT cluster_id, label, member_count FROM face_clusters ORDER BY cluster_id",
    "SELECT cluster_id, confirmed_name, confirmed_at FROM confirmations ORDER BY cluster_id",
)


def _face_data_lines(layout: ArchivLayout) -> Iterator[bytes]:
    faces = face_index_path(layout)
    if not faces.is_file():
        yield b"faces none\n"
        return
    connection = sqlite3.connect(f"file:{faces}?mode=ro", uri=True)
    try:
        for query in _FACE_QUERIES:
            try:
                rows = connection.execute(query).fetchall()
            except sqlite3.OperationalError:
                yield b"faces table missing\n"
                continue
            for row in rows:
                yield ("face " + "|".join(str(value) for value in row) + "\n").encode()
    finally:
        connection.close()
