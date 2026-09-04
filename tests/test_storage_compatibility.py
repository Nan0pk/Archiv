from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from zipfile import BadZipFile, ZipFile

import pytest

from archiv.archive import create_archive, restore_archive
from archiv.storage.database import SCHEMA, SCHEMA_VERSION, ArchivDatabase, SchemaVersionError
from archiv.storage.integrity import inspect_home
from archiv.storage.layout import ArchivLayout

FIXTURES = Path(__file__).parent / "fixtures" / "storage"


@pytest.mark.parametrize("version", range(SCHEMA_VERSION + 1))
def test_every_supported_schema_fixture_opens_or_upgrades(tmp_path: Path, version: int) -> None:
    database = tmp_path / "archiv.sqlite3"
    with sqlite3.connect(database) as connection:
        if version == SCHEMA_VERSION:
            connection.executescript(SCHEMA)
        connection.executescript((FIXTURES / f"schema-{version}.sql").read_text())
    ArchivDatabase(database).initialize()
    assert ArchivDatabase(database).schema_version() == SCHEMA_VERSION
    assert {"objects", "ingestions", "processing_runs", "processing_queue"} <= {
        row[0] for row in sqlite3.connect(database).execute("SELECT name FROM sqlite_master")
    }
    if version < SCHEMA_VERSION:
        assert ArchivDatabase(database).recovery_path.is_file()


def test_interrupted_migration_is_restartable(tmp_path: Path) -> None:
    database = tmp_path / "archiv.sqlite3"
    database.touch()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE objects_probe(value TEXT)")
        connection.rollback()  # simulate interruption before the schema-version commit
    ArchivDatabase(database).initialize()
    assert ArchivDatabase(database).schema_version() == SCHEMA_VERSION


def test_newer_database_is_rejected_with_downgrade_guidance(tmp_path: Path) -> None:
    database = tmp_path / "archiv.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaVersionError, match="never downgrade"):
        ArchivDatabase(database).initialize()


def test_corrupt_object_and_truncated_backup_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    ArchivDatabase(layout.database).initialize()
    object_path = layout.original_path("a" * 64)
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"corrupt")
    with sqlite3.connect(layout.database) as connection:
        connection.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("a" * 64, 7, "text/plain", ".txt", str(object_path), "now"),
        )
    assert not inspect_home(home)["ok"]
    object_path.unlink()
    with sqlite3.connect(layout.database) as connection:
        connection.execute("DELETE FROM objects")
    backup = tmp_path / "backup.zip"
    create_archive(backup, home=home)
    backup.write_bytes(backup.read_bytes()[:20])
    with pytest.raises(BadZipFile):
        restore_archive(backup, home=tmp_path / "restored")


def test_restore_empty_and_replace_populated_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    layout = ArchivLayout.resolve(source)
    layout.ensure()
    ArchivDatabase(layout.database).initialize()
    backup = tmp_path / "backup.zip"
    create_archive(backup, home=source)
    empty = tmp_path / "empty"
    restore_archive(backup, home=empty)
    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "old").write_text("recovery")
    restore_archive(backup, home=populated, replace=True)
    assert populated.with_name(".populated.pre-restore").joinpath("old").is_file()
    with ZipFile(backup) as archive:
        assert "layout-version" in archive.namelist()


def test_backup_rejects_insufficient_disk_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    ArchivDatabase(layout.database).initialize()

    def no_free_space(_path: object) -> SimpleNamespace:
        return SimpleNamespace(free=0)

    monkeypatch.setattr(shutil, "disk_usage", no_free_space)
    with pytest.raises(OSError, match="insufficient disk space"):
        create_archive(tmp_path / "backup.zip", home=home)
