from __future__ import annotations

import json
from pathlib import Path

from report_support import prepare_report_archive
from typer.testing import CliRunner

from archiv.cli import app

runner = CliRunner()


def test_generate_and_verify_report_cli(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    prepare_report_archive(ingestion_corpus, home)
    output = tmp_path / "cli-report.docx"

    generated = runner.invoke(
        app,
        [
            "generate-report",
            "MARKER",
            str(output),
            "--home",
            str(home),
            "--max-sources",
            "4",
            "--no-render",
        ],
    )
    assert generated.exit_code == 0, generated.output
    payload = json.loads(generated.output)
    assert payload["status"] == "succeeded"

    verified = runner.invoke(
        app,
        [
            "verify-report",
            str(output),
            payload["manifest_path"],
            "--home",
            str(home),
            "--no-render",
        ],
    )
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True
