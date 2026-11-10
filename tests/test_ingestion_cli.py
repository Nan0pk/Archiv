from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app


def test_cli_ingest_and_rebuild(ingestion_corpus: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    home = tmp_path / "archiv-home"
    ingestion = runner.invoke(
        app,
        ["ingest", str(ingestion_corpus / "plain-text.txt"), "--home", str(home)],
    )
    assert ingestion.exit_code == 0, ingestion.output
    payload = json.loads(ingestion.output)
    digest = payload["object_sha256"]

    rebuild = runner.invoke(app, ["rebuild-derived", digest, "--home", str(home)])
    assert rebuild.exit_code == 0, rebuild.output
    assert json.loads(rebuild.output)[0]["status"] == "succeeded"


def test_cli_ingest_directory_leaves_out_office_lock_files(tmp_path: Path) -> None:
    source = tmp_path / "inbox"
    source.mkdir()
    (source / "notes.txt").write_text(
        "Meeting notes about the harvest festival.\n", encoding="utf-8"
    )
    (source / "~$report.docx").write_bytes(b"\x07lock")  # what Word leaves beside an open file
    result = CliRunner().invoke(app, ["ingest", str(source), "--home", str(tmp_path / "home")])
    assert result.exit_code == 0, result.output
    ingested = json.loads(result.output)["ingested"]
    assert len(ingested) == 1  # the lock file is not tried and rejected as a broken document
