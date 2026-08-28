from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.doctor import diagnostics_report, doctor_report
from archiv.storage.database import ArchivDatabase

runner = CliRunner()


def test_doctor_report_passes_minimum_environment() -> None:
    report = doctor_report()

    assert report["status"] == "ok"
    checks = report["checks"]
    assert isinstance(checks, list)
    assert {check["name"] for check in checks} == {
        "python",
        "sqlite_fts5",
        "writable_workspace",
    }
    assert all(check["passed"] for check in checks)


def test_doctor_json_is_machine_readable() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["status"] == "ok"


def test_diagnostics_bundle_excludes_private_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home-private-person"
    home.mkdir()
    database = home / "archiv.sqlite3"
    ArchivDatabase(database).initialize()
    secrets = [
        "/home/alice/Tax Return 2025.pdf",
        "Tax Return 2025.pdf",
        "What is Alice's account balance?",
        "The answer is 42 and this is an excerpt.",
        "https://user:password@localhost:11434/v1",
        "sk-private-credential",
    ]
    monkeypatch.setenv("ARCHIV_API_KEY", secrets[-1])
    monkeypatch.setenv("PRIVATE_QUESTION", secrets[2])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("a" * 64, 1, "text/plain", ".txt", secrets[0], "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO ingestions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "private-id",
                "a" * 64,
                secrets[0],
                secrets[1],
                "2026-01-01",
                0,
                "failed",
                "Permission denied: " + secrets[3],
            ),
        )
        connection.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run",
                "a" * 64,
                "model",
                "1",
                json.dumps({"prompt": secrets[2], "endpoint": secrets[4]}),
                "failed",
                "text",
                secrets[0],
                None,
                "timeout " + secrets[5],
                "a",
                "b",
            ),
        )
        connection.execute(
            "INSERT INTO ingestion_failures VALUES (?, ?, ?, ?, ?, ?)",
            (
                "rejected-attempt",
                secrets[0],
                secrets[1],
                None,
                "PermissionError: " + secrets[3],
                "2026-01-01",
            ),
        )
    serialized = json.dumps(diagnostics_report(home), sort_keys=True)
    assert all(secret not in serialized for secret in secrets)
    assert (
        '"permission": 2' in serialized
    )  # existing ingestions row + the new ingestion_failures row
    assert '"timeout": 1' in serialized

    output = tmp_path / "support.json"
    result = runner.invoke(app, ["diagnostics-export", str(output), "--home", str(home), "--yes"])
    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8").strip() in result.stdout
    assert all(
        secret not in result.stdout and secret not in output.read_text() for secret in secrets
    )


def test_diagnostics_counts_pre_storage_failures(tmp_path: Path) -> None:
    """A file rejected before storage has no ingestions row; diagnostics must still see it."""

    home = tmp_path / "home"
    vault = tmp_path / "vault"
    vault.mkdir()
    vault.joinpath("broken.docx").write_bytes(b"not a real docx package")

    added = runner.invoke(app, ["add", str(vault), "--home", str(home), "--json"])
    assert added.exit_code == 1, added.output  # nothing ingestible; add fails closed

    serialized = json.dumps(diagnostics_report(home), sort_keys=True)
    assert '"ingestion_states": {"failed": 1}' in serialized
