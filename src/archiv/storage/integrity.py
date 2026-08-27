"""Read-only integrity inspection for the durable ARCHIV_HOME contract."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import TypedDict

from archiv.hashing import sha256_file
from archiv.storage.database import SCHEMA_VERSION
from archiv.storage.layout import ArchivLayout


class IntegrityReport(TypedDict):
    ok: bool
    database: str
    schema_version: int | None
    canonical_objects: dict[str, int]
    evidence: dict[str, int]
    available_bytes: int
    orphaned_temporary: list[str]
    errors: list[str]


def inspect_home(home: Path | None = None) -> IntegrityReport:
    """Verify SQLite, content addresses, and machine-readable durable evidence."""

    layout = ArchivLayout.resolve(home)
    errors: list[str] = []
    checked = corrupt = evidence_checked = evidence_invalid = 0
    schema_version: int | None = None
    database_status = "absent"
    if layout.database.is_file():
        try:
            with sqlite3.connect(f"file:{layout.database}?mode=ro", uri=True) as connection:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if integrity != "ok":
                    errors.append(f"database integrity_check: {integrity}")
                if foreign_keys:
                    errors.append(f"database foreign keys: {len(foreign_keys)} violation(s)")
                if schema_version > SCHEMA_VERSION:
                    errors.append(
                        f"database schema {schema_version} is newer than supported {SCHEMA_VERSION}"
                    )
                rows = connection.execute("SELECT sha256, storage_path FROM objects").fetchall()
            database_status = "ok" if not errors else "failed"
            for digest, storage_path in rows:
                checked += 1
                path = Path(str(storage_path))
                if not path.is_file() or sha256_file(path) != digest:
                    corrupt += 1
                    errors.append(f"canonical object failed hash validation: {digest}")
        except sqlite3.Error as error:
            database_status = "failed"
            errors.append(f"database: {error}")

    for root_name in ("derived", "runs", "outputs"):
        root = layout.root / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            evidence_checked += 1
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, (dict, list)):
                    raise ValueError("top level is not an object or array")
            except (OSError, UnicodeError, ValueError) as error:
                evidence_invalid += 1
                relative = path.relative_to(layout.root)
                errors.append(f"invalid normalized evidence {relative}: {error}")

    orphans = []
    if layout.temporary.is_dir():
        orphans = sorted(str(path.relative_to(layout.root)) for path in layout.temporary.iterdir())
    for path in layout.root.glob(".*.tmp") if layout.root.is_dir() else ():
        orphans.append(str(path.relative_to(layout.root)))
    if orphans:
        errors.append(f"orphaned temporary state: {len(orphans)} item(s); safe to remove")
    available = shutil.disk_usage(layout.root if layout.root.exists() else layout.root.parent).free
    return {
        "ok": not errors,
        "database": database_status,
        "schema_version": schema_version,
        "canonical_objects": {"checked": checked, "corrupt": corrupt},
        "evidence": {"checked": evidence_checked, "invalid": evidence_invalid},
        "available_bytes": available,
        "orphaned_temporary": sorted(orphans),
        "errors": errors,
    }
