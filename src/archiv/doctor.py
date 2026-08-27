"""Environment checks used by the CLI and CI smoke tests."""

from __future__ import annotations

import platform
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal, TypedDict

from archiv import __version__
from archiv.storage.integrity import inspect_home
from archiv.storage.layout import ArchivLayout

SUPPORT_BUNDLE_SCHEMA_VERSION = "1"
_DEPENDENCIES = (
    "mcp",
    "openpyxl",
    "pillow",
    "pydantic",
    "pypdf",
    "python-docx",
    "python-pptx",
    "rich",
    "typer",
)


@dataclass(frozen=True)
class CheckResult:
    """One deterministic environment check."""

    name: str
    passed: bool
    detail: str


class CheckPayload(TypedDict):
    """Serializable check result."""

    name: str
    passed: bool
    detail: str


class DoctorReport(TypedDict):
    """Serializable environment report."""

    status: Literal["ok", "failed"]
    checks: list[CheckPayload]


def _python_check() -> CheckResult:
    current = sys.version_info[:3]
    passed = current >= (3, 12, 0)
    return CheckResult("python", passed, f"{current[0]}.{current[1]}.{current[2]}")


def _sqlite_fts5_check() -> CheckResult:
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
            connection.execute("INSERT INTO probe(body) VALUES (?)", ("ARCHIV_FTS_OK",))
            match = connection.execute(
                "SELECT body FROM probe WHERE probe MATCH ?", ("ARCHIV_FTS_OK",)
            ).fetchone()
        passed = match == ("ARCHIV_FTS_OK",)
        return CheckResult("sqlite_fts5", passed, sqlite3.sqlite_version)
    except sqlite3.Error as error:
        return CheckResult("sqlite_fts5", False, str(error))


def _writable_workspace_check() -> CheckResult:
    try:
        with tempfile.TemporaryDirectory(prefix="archiv-doctor-") as directory:
            probe = Path(directory) / "probe.txt"
            probe.write_text("ARCHIV_WRITE_OK\n", encoding="utf-8")
            passed = probe.read_text(encoding="utf-8") == "ARCHIV_WRITE_OK\n"
        return CheckResult("writable_workspace", passed, "temporary workspace round-trip")
    except OSError as error:
        return CheckResult("writable_workspace", False, str(error))


def collect_checks(home: Path | None = None) -> list[CheckResult]:
    """Return all checks without mutating persistent user state."""

    checks = [_python_check(), _sqlite_fts5_check(), _writable_workspace_check()]
    if home is not None:
        integrity = inspect_home(home)
        checks.append(
            CheckResult(
                "archiv_home_integrity",
                integrity["ok"],
                "; ".join(integrity["errors"]) or "ok",
            )
        )
    return checks


def doctor_report(home: Path | None = None) -> DoctorReport:
    """Return a machine-readable doctor report."""

    checks = collect_checks(home)
    payload: list[CheckPayload] = [
        {"name": check.name, "passed": check.passed, "detail": check.detail} for check in checks
    ]
    return {
        "status": "ok" if all(check.passed for check in checks) else "failed",
        "checks": payload,
    }


def _dependency_status() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for package in _DEPENDENCIES:
        try:
            result[package] = {"status": "installed", "version": version(package)}
        except PackageNotFoundError:
            result[package] = {"status": "missing", "version": "not-installed"}
    return result


def _category(value: object) -> str:
    """Reduce an untrusted ledger error to a fixed, content-free category."""

    lowered = str(value or "").lower()
    rules = (
        ("permission", "permission"),
        ("not found", "missing_input"),
        ("no such file", "missing_input"),
        ("timeout", "timeout"),
        ("unsupported", "unsupported_format"),
        ("decode", "decode"),
        ("parse", "parse"),
        ("validation", "validation"),
    )
    return next((category for marker, category in rules if marker in lowered), "other")


def diagnostics_report(home: Path | None = None) -> dict[str, object]:
    """Collect an allow-listed, aggregate-only support report.

    Never serialize environment values, configuration, row identifiers, paths,
    source names, free-form errors, document content, or model traffic.
    """

    layout = ArchivLayout.resolve(home)
    checks = doctor_report()
    ingestion: Counter[str] = Counter()
    processing: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    database_schema = "absent"
    database_status = "absent"
    if layout.database.is_file():
        database_status = "readable"
        try:
            with sqlite3.connect(f"file:{layout.database}?mode=ro", uri=True) as connection:
                database_schema = str(connection.execute("PRAGMA user_version").fetchone()[0])
                ingestion.update(
                    str(row[0]) for row in connection.execute("SELECT status FROM ingestions")
                )
                processing.update(
                    str(row[0]) for row in connection.execute("SELECT status FROM processing_runs")
                )
                errors.update(
                    _category(row[0])
                    for row in connection.execute(
                        "SELECT error FROM ingestions WHERE error IS NOT NULL"
                    )
                )
                errors.update(
                    _category(row[0])
                    for row in connection.execute(
                        "SELECT error FROM processing_runs WHERE error IS NOT NULL"
                    )
                )
        except sqlite3.Error:
            database_status = "unreadable"
            database_schema = "unknown"
    return {
        "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        "product": {"name": "Archiv", "version": __version__},
        "platform": {
            "system": platform.system() or "unknown",
            "release": platform.release() or "unknown",
            "machine": platform.machine() or "unknown",
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "dependencies": _dependency_status(),
        "schema_versions": {
            "support_bundle": SUPPORT_BUNDLE_SCHEMA_VERSION,
            "database": database_schema,
            "format_compatibility": "1",
        },
        "storage": {"database_status": database_status},
        "ingestion_states": dict(sorted(ingestion.items())),
        "error_categories": dict(sorted(errors.items())),
        "validation_outcomes": {
            "doctor": {
                "passed": sum(item["passed"] for item in checks["checks"]),
                "failed": sum(not item["passed"] for item in checks["checks"]),
            },
            "processing": dict(sorted(processing.items())),
        },
    }


def save_diagnostics(report: dict[str, object], destination: Path) -> None:
    """Save exactly the already-previewed report, refusing overwrite."""

    import json

    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
