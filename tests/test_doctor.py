from __future__ import annotations

import json

from typer.testing import CliRunner

from archiv.cli import app
from archiv.doctor import doctor_report

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
