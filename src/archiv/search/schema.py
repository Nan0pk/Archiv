"""SQLite FTS5 schema for the replaceable local search index."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE index_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE segments (
    rowid INTEGER PRIMARY KEY,
    segment_id TEXT NOT NULL UNIQUE CHECK(length(segment_id) = 64),
    segment_index INTEGER NOT NULL CHECK(segment_index >= 0),
    object_sha256 TEXT NOT NULL CHECK(length(object_sha256) = 64),
    source_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    kind TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL CHECK(length(text_sha256) = 64),
    normalized_path TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL CHECK(length(normalized_sha256) = 64)
);
CREATE INDEX segments_object_idx ON segments(object_sha256);
CREATE INDEX segments_source_idx ON segments(source_name);
CREATE INDEX segments_media_idx ON segments(media_type);
CREATE INDEX segments_kind_idx ON segments(kind);
CREATE VIRTUAL TABLE segments_fts USING fts5(
    text,
    content='segments',
    content_rowid='rowid',
    tokenize='unicode61'
);
"""


@contextmanager
def connect_index(path: Path) -> Generator[sqlite3.Connection]:
    """Open one short-lived search-index connection."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()
