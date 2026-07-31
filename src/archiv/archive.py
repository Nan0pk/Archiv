"""Verified portable backup, export, and restore for Archiv durable state."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pydantic import Field

from archiv.contracts import StrictModel
from archiv.hashing import sha256_bytes, sha256_file
from archiv.search import rebuild_search_index
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout

ARCHIVE_MANIFEST = "archive-manifest.json"
DURABLE_DIRECTORIES = ("originals", "derived", "runs", "outputs", "config")
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ArchiveEntry(StrictModel):
    """Hash evidence for one member of a portable Archiv archive."""

    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArchiveManifest(StrictModel):
    """Versioned manifest for a verified durable-state archive."""

    schema_version: str = "1"
    kind: Literal["backup", "portable-export"]
    created_at: str
    source_root: str
    entries: list[ArchiveEntry]
    excluded_rebuildable_paths: list[str] = Field(default_factory=lambda: ["indexes", "temporary"])


class ArchiveResult(StrictModel):
    """Terminal backup/export evidence."""

    schema_version: str = "1"
    kind: Literal["backup", "portable-export"]
    archive_path: str
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entry_count: int = Field(ge=0)
    source_root: str


class RestoreResult(StrictModel):
    """Terminal restore evidence."""

    schema_version: str = "1"
    archive_path: str
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_root: str
    restored_entries: int = Field(ge=0)
    search_index_rebuilt: bool


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=_FIXED_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _checkpoint_database(layout: ArchivLayout, destination: Path) -> None:
    database = ArchivDatabase(layout.database)
    database.initialize()
    with database.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    source = sqlite3.connect(layout.database)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _durable_files(layout: ArchivLayout, database_snapshot: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = [("archiv.sqlite3", database_snapshot)]
    for directory_name in DURABLE_DIRECTORIES:
        root = layout.root / directory_name
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(layout.root).as_posix()
            files.append((relative, path))
    return files


def create_archive(
    output: Path,
    *,
    home: Path | None = None,
    kind: Literal["backup", "portable-export"] = "backup",
) -> ArchiveResult:
    """Create a deterministic verified ZIP containing only durable Archiv state."""

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".zip":
        raise ValueError("Archiv archives must use the .zip extension")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="archiv-archive-") as directory:
        snapshot = Path(directory) / "archiv.sqlite3"
        _checkpoint_database(layout, snapshot)
        files = _durable_files(layout, snapshot)
        entries = [
            ArchiveEntry(path=name, size=path.stat().st_size, sha256=sha256_file(path))
            for name, path in files
        ]
        manifest = ArchiveManifest(
            kind=kind,
            created_at=datetime.now(UTC).isoformat(),
            source_root=str(layout.root),
            entries=entries,
        )
        temporary = output.with_name(f".{output.name}.tmp")
        with ZipFile(temporary, "w") as archive:
            for name, path in files:
                archive.writestr(_zip_info(name), path.read_bytes())
            manifest_bytes = (
                json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            archive.writestr(_zip_info(ARCHIVE_MANIFEST), manifest_bytes)
        os.replace(temporary, output)

    return ArchiveResult(
        kind=kind,
        archive_path=str(output),
        archive_sha256=sha256_file(output),
        entry_count=len(entries),
        source_root=str(layout.root),
    )


def _safe_member(name: str) -> Path:
    candidate = PurePosixPath(name)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError(f"unsafe archive member: {name}")
    if name == ARCHIVE_MANIFEST:
        raise ValueError("manifest cannot be restored as durable state")
    if candidate.parts[0] not in {*DURABLE_DIRECTORIES, "archiv.sqlite3"}:
        raise ValueError(f"archive contains unsupported state path: {name}")
    return Path(*candidate.parts)


def _rewrite_json_paths(root: Path, source_root: str, target_root: str) -> None:
    if source_root == target_root:
        return
    for path in sorted(root.rglob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if source_root in text:
            path.write_text(text.replace(source_root, target_root), encoding="utf-8")


def _relocate_database(database_path: Path, source_root: str, target_root: str) -> None:
    if source_root == target_root:
        return
    with sqlite3.connect(database_path) as connection:
        for table, column in (
            ("objects", "storage_path"),
            ("ingestions", "source_path"),
            ("processing_runs", "output_path"),
        ):
            connection.execute(
                f"UPDATE {table} SET {column} = replace({column}, ?, ?) "
                f"WHERE {column} IS NOT NULL AND {column} LIKE ?",
                (source_root, target_root, f"{source_root}%"),
            )
        connection.commit()


def restore_archive(
    archive_path: Path,
    *,
    home: Path | None = None,
) -> RestoreResult:
    """Restore verified durable state into an empty Archiv home and rebuild indexes."""

    archive_path = archive_path.expanduser().resolve(strict=True)
    layout = ArchivLayout.resolve(home)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise FileExistsError("restore target must be absent or empty")
    layout.root.mkdir(parents=True, exist_ok=True)

    manifest: ArchiveManifest
    try:
        with ZipFile(archive_path) as archive:
            try:
                manifest = ArchiveManifest.model_validate_json(
                    archive.read(ARCHIVE_MANIFEST).decode("utf-8")
                )
            except KeyError as error:
                raise ValueError("archive manifest is missing") from error
            declared = {entry.path: entry for entry in manifest.entries}
            actual = {name for name in archive.namelist() if name != ARCHIVE_MANIFEST}
            if actual != set(declared):
                raise ValueError("archive members do not match the manifest")
            for name in sorted(actual):
                relative = _safe_member(name)
                data = archive.read(name)
                evidence = declared[name]
                if len(data) != evidence.size or sha256_bytes(data) != evidence.sha256:
                    raise ValueError(f"archive member failed hash validation: {name}")
                destination = layout.root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
    except Exception:
        shutil.rmtree(layout.root, ignore_errors=True)
        raise

    _rewrite_json_paths(layout.root, manifest.source_root, str(layout.root))
    _relocate_database(layout.database, manifest.source_root, str(layout.root))
    for original in layout.originals.rglob("*"):
        if original.is_file():
            os.chmod(original, 0o444)
    rebuild_search_index(home=layout.root)
    return RestoreResult(
        archive_path=str(archive_path),
        archive_sha256=sha256_file(archive_path),
        target_root=str(layout.root),
        restored_entries=len(manifest.entries),
        search_index_rebuilt=True,
    )
