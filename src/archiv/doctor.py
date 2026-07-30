"""Environment checks used by the CLI and CI smoke tests."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict


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


def collect_checks() -> list[CheckResult]:
    """Return all checks without mutating persistent user state."""

    return [_python_check(), _sqlite_fts5_check(), _writable_workspace_check()]


def doctor_report() -> DoctorReport:
    """Return a machine-readable doctor report."""

    checks = collect_checks()
    payload: list[CheckPayload] = [
        {"name": check.name, "passed": check.passed, "detail": check.detail} for check in checks
    ]
    return {
        "status": "ok" if all(check.passed for check in checks) else "failed",
        "checks": payload,
    }
