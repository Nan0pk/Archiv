"""The memory database: things, their written forms, relations, merges, and model outputs."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from archiv.memory.extract import Extraction, ident
from archiv.storage.layout import ArchivLayout

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outputs (key TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS things (
    thing_id INTEGER PRIMARY KEY, ident TEXT NOT NULL UNIQUE, form TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forms (
    thing_id INTEGER NOT NULL, form TEXT NOT NULL, kind TEXT NOT NULL,
    segment_id TEXT NOT NULL, object_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS forms_thing ON forms(thing_id);
CREATE INDEX IF NOT EXISTS forms_object ON forms(object_sha256);
CREATE INDEX IF NOT EXISTS forms_segment ON forms(segment_id);
CREATE TABLE IF NOT EXISTS relations (
    source_id INTEGER NOT NULL, phrase TEXT NOT NULL, target_id INTEGER NOT NULL,
    segment_id TEXT NOT NULL, object_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relations_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS relations_target ON relations(target_id);
CREATE INDEX IF NOT EXISTS relations_object ON relations(object_sha256);
CREATE TABLE IF NOT EXISTS merges (
    a_id INTEGER NOT NULL, b_id INTEGER NOT NULL, same INTEGER NOT NULL,
    reason TEXT NOT NULL, segment_ids_json TEXT NOT NULL, PRIMARY KEY (a_id, b_id)
);
"""


def memory_path(layout: ArchivLayout) -> Path:
    return layout.indexes / "memory.sqlite3"


@contextmanager
def open_memory(layout: ArchivLayout) -> Generator[sqlite3.Connection]:
    """Open the memory database, creating its tables if needed; commit and close after."""
    layout.indexes.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(memory_path(layout))
    try:
        connection.executescript(_SCHEMA)
        yield connection
        connection.commit()
    finally:
        connection.close()


def cache_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def cached(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT text FROM outputs WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def remember(conn: sqlite3.Connection, key: str, text: str) -> None:
    conn.execute("INSERT OR REPLACE INTO outputs (key, text) VALUES (?, ?)", (key, text))


def thing_id_for(conn: sqlite3.Connection, form: str) -> int:
    key = ident(form)
    conn.execute("INSERT OR IGNORE INTO things (ident, form) VALUES (?, ?)", (key, form))
    row = conn.execute("SELECT thing_id FROM things WHERE ident = ?", (key,)).fetchone()
    return int(row[0])


def store_extraction(conn: sqlite3.Connection, object_sha256: str, extraction: Extraction) -> None:
    """Replace everything stored for one document with this extraction."""
    conn.execute("DELETE FROM forms WHERE object_sha256 = ?", (object_sha256,))
    conn.execute("DELETE FROM relations WHERE object_sha256 = ?", (object_sha256,))
    for mention in extraction.mentions:
        conn.execute(
            "INSERT INTO forms (thing_id, form, kind, segment_id, object_sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                thing_id_for(conn, mention.form),
                mention.form,
                mention.kind,
                mention.segment_id,
                object_sha256,
            ),
        )
    for relation in extraction.relations:
        conn.execute(
            "INSERT INTO relations (source_id, phrase, target_id, segment_id, object_sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                thing_id_for(conn, relation.source),
                relation.phrase,
                thing_id_for(conn, relation.target),
                relation.segment_id,
                object_sha256,
            ),
        )
