"""A bulk `archiv add` skips the disk sync after every database write, and syncs once at the end.

Measured before this change: saving one file's records took about 12 ms, and 91 percent of
that was the database waiting for the disk, four times per file. For a folder of ten thousand
Word files that wait alone was two minutes, in the one process that cannot be spread over
workers. The records are still written one file at a time; what changes is when they reach
the disk. When `add` returns, everything it wrote has been synced, exactly as before. The
only difference is in a power cut or a kernel crash *during* an `add`: the last few finished
files may be missing from the ledger, which is consistent either way, and running `add`
again finishes them because files already stored are recognised.
"""

from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.storage import database as database_module
from archiv.storage.database import ArchivDatabase

runner = CliRunner()


def _database(tmp_path: Path) -> ArchivDatabase:
    database = ArchivDatabase(tmp_path / "ledger.sqlite3")
    database.initialize()
    return database


def _sync_mode(database: ArchivDatabase) -> int:
    with database.connect() as connection:
        return int(connection.execute("PRAGMA synchronous").fetchone()[0])


def _log_is_in_the_main_file(path: Path) -> bool:
    """True when nothing is left waiting in the write-ahead log."""
    connection = sqlite3.connect(path)
    try:
        _busy, log, checkpointed = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    finally:
        connection.close()
    return log == checkpointed


def _add_note(database: ArchivDatabase, name: str) -> None:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO ingestion_failures (failure_id, source_path, source_name, error, "
            "attempted_at) VALUES (?, ?, ?, 'x', 'now')",
            (name, f"/x/{name}", name),
        )
        connection.commit()


def test_every_write_is_synced_by_default(tmp_path: Path) -> None:
    assert _sync_mode(_database(tmp_path)) == 2  # FULL


def test_bulk_write_relaxes_the_sync_only_inside_it(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with database.bulk_write():
        assert _sync_mode(database) == 1  # NORMAL
    assert _sync_mode(database) == 2


def test_bulk_write_ends_with_everything_in_the_main_file(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with database.bulk_write():
        for number in range(25):
            _add_note(database, f"n{number}")
    assert _log_is_in_the_main_file(database.path)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingestion_failures").fetchone()[0] == 25


def test_bulk_write_restores_and_syncs_when_the_body_fails(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(RuntimeError, match="stopped half way"), database.bulk_write():
        _add_note(database, "before")
        raise RuntimeError("stopped half way")
    assert _sync_mode(database) == 2
    assert _log_is_in_the_main_file(database.path)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingestion_failures").fetchone()[0] == 1


def test_a_reader_that_blocks_the_checkpoint_does_not_lose_the_final_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process reading at the wrong moment must not leave the tail unsynced."""
    monkeypatch.setattr(database_module, "_CHECKPOINT_WAIT_SECONDS", 0.05)
    synced: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{fd}"))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    database = _database(tmp_path)
    reader = sqlite3.connect(database.path)
    try:
        with database.bulk_write():
            _add_note(database, "one")
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM ingestion_failures").fetchone()
            _add_note(database, "two")  # lands after the reader's snapshot
    finally:
        reader.close()
    assert any(name.endswith("ledger.sqlite3-wal") for name in synced), synced
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ingestion_failures").fetchone()[0] == 2


def test_bulk_write_inside_bulk_write_changes_nothing(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with database.bulk_write(), database.bulk_write():
        assert _sync_mode(database) == 1
        _add_note(database, "inner")
    assert _sync_mode(database) == 2
    assert _log_is_in_the_main_file(database.path)


@pytest.fixture
def bulk_entries(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    entries: list[int] = []
    original = ArchivDatabase.bulk_write

    @contextmanager
    def counting(self: ArchivDatabase) -> Generator[None]:
        entries.append(1)
        with original(self):
            yield

    monkeypatch.setattr(ArchivDatabase, "bulk_write", counting)
    return entries


def _folder_with_an_archive(root: Path) -> Path:
    folder = root / "mixed"
    folder.mkdir()
    for number in range(4):
        (folder / f"note-{number}.txt").write_text(f"A note numbered {number}.\n", encoding="utf-8")
    with zipfile.ZipFile(folder / "bundle.zip", "w") as bundle:
        bundle.writestr("inside-one.txt", "First file inside the bundle.\n")
        bundle.writestr("inside-two.txt", "Second file inside the bundle.\n")
    return folder


def test_adding_a_folder_syncs_once_at_the_end_and_still_unpacks_archives(
    tmp_path: Path, bulk_entries: list[int]
) -> None:
    folder = _folder_with_an_archive(tmp_path)
    home = tmp_path / "home"
    result = runner.invoke(app, ["add", str(folder), "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["failed"] == 0
    assert bulk_entries == [1]
    database_path = home / "archiv.sqlite3"
    assert _log_is_in_the_main_file(database_path)
    connection = sqlite3.connect(database_path)
    try:
        members = connection.execute("SELECT COUNT(*) FROM containment").fetchone()[0]
        succeeded = connection.execute(
            "SELECT COUNT(*) FROM ingestions WHERE status = 'succeeded'"
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM ingestions WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert members == 2  # the two files inside the bundle were recorded and linked to it
    assert succeeded == 4 + 1 + 2 and pending == 0


def test_adding_a_single_file_keeps_the_full_sync(tmp_path: Path, bulk_entries: list[int]) -> None:
    single = tmp_path / "one.txt"
    single.write_text("Just one file.\n", encoding="utf-8")
    result = runner.invoke(app, ["add", str(single), "--home", str(tmp_path / "home"), "--json"])
    assert result.exit_code == 0, result.output
    assert bulk_entries == []
