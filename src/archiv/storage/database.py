"""Versioned SQLite durable metadata and processing ledger."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2
MIN_SUPPORTED_SCHEMA_VERSION = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64), size INTEGER NOT NULL CHECK(size >= 0),
    media_type TEXT NOT NULL, source_extension TEXT NOT NULL,
    storage_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestions (
    ingestion_id TEXT PRIMARY KEY, object_sha256 TEXT NOT NULL REFERENCES objects(sha256),
    source_path TEXT NOT NULL, source_name TEXT NOT NULL, imported_at TEXT NOT NULL,
    duplicate INTEGER NOT NULL CHECK(duplicate IN (0, 1)),
    status TEXT NOT NULL CHECK(status IN ('pending', 'succeeded', 'failed')), error TEXT
);
CREATE TABLE IF NOT EXISTS processing_runs (
    processing_id TEXT PRIMARY KEY, object_sha256 TEXT NOT NULL REFERENCES objects(sha256),
    processor TEXT NOT NULL, processor_version TEXT NOT NULL, parameters_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'skipped')),
    output_kind TEXT NOT NULL, output_path TEXT, output_sha256 TEXT, error TEXT,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestions_object_idx ON ingestions(object_sha256);
CREATE INDEX IF NOT EXISTS processing_object_idx ON processing_runs(object_sha256);
CREATE TABLE IF NOT EXISTS ingestion_failures (
    failure_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, source_name TEXT NOT NULL,
    object_sha256 TEXT, error TEXT NOT NULL, attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestion_failures_time_idx ON ingestion_failures(attempted_at);
"""


class SchemaVersionError(RuntimeError):
    """Database cannot safely be opened by this Archiv version."""


def _migration_0_to_1(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)


def _migration_1_to_2(connection: sqlite3.Connection) -> None:
    """Add a ledger for validation failures that never reach the ingestions table.

    A file rejected before storage (malformed input, an oversized document) leaves
    no row in ``objects`` or ``ingestions`` by design -- that keeps a rejected file
    from ever appearing to have been partially archived. This table is the only
    durable record that the attempt happened at all.
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_failures (
            failure_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, source_name TEXT NOT NULL,
            object_sha256 TEXT, error TEXT NOT NULL, attempted_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ingestion_failures_time_idx ON ingestion_failures(attempted_at)"
    )


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migration_0_to_1,
    1: _migration_1_to_2,
}


class ArchivDatabase:
    """SQLite boundary which upgrades all supported historical schemas."""

    def __init__(self, path: Path):
        self.path = path

    @property
    def recovery_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".pre-migration")

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def schema_version(self) -> int:
        if not self.path.exists():
            return 0
        with sqlite3.connect(self.path) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def initialize(self) -> None:
        """Create or migrate atomically, retaining the pre-migration database copy.

        Each step is one transaction and advances ``user_version`` last. Re-running
        after interruption therefore repeats only an incomplete idempotent step.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        version = self.schema_version()
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database schema {version} is newer than supported schema {SCHEMA_VERSION}; "
                "upgrade Archiv or restore a backup made by this Archiv version "
                "(never downgrade in place)"
            )
        if version < MIN_SUPPORTED_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"database schema {version} is no longer supported; install an intermediate Archiv "
                "release and migrate it before upgrading"
            )
        existed = self.path.exists()
        if existed and version < SCHEMA_VERSION and not self.recovery_path.exists():
            with (
                sqlite3.connect(self.path) as source,
                sqlite3.connect(self.recovery_path) as target,
            ):
                source.backup(target)
            os.chmod(self.recovery_path, 0o600)
        with self.connect() as connection:
            while version < SCHEMA_VERSION:
                migration = MIGRATIONS.get(version)
                if migration is None:
                    raise SchemaVersionError(f"no migration path from database schema {version}")
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    migration(connection)
                    connection.execute(f"PRAGMA user_version = {version + 1}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                version += 1
        if not existed:
            self.recovery_path.unlink(missing_ok=True)

    def recover_pre_migration(self) -> None:
        """Replace the database with its retained recovery point."""

        if not self.recovery_path.is_file():
            raise FileNotFoundError("no pre-migration recovery point exists")
        shutil.copy2(self.recovery_path, self.path)
