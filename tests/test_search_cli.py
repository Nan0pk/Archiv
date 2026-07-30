from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app


def test_cli_rebuild_index_and_search(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    home = tmp_path / "archiv-home"
    ingestion = runner.invoke(
        app,
        ["ingest", str(ingestion_corpus / "document.docx"), "--home", str(home)],
    )
    assert ingestion.exit_code == 0, ingestion.output

    rebuild = runner.invoke(app, ["rebuild-search-index", "--home", str(home)])
    assert rebuild.exit_code == 0, rebuild.output
    build_payload = json.loads(rebuild.output)
    assert build_payload["object_count"] == 1
    assert build_payload["segment_count"] >= 1

    result = runner.invoke(
        app,
        [
            "search",
            "ARCHIV-DOCX-MARKER-2026",
            "--home",
            str(home),
            "--source-name",
            "document.docx",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["citation"]["locator"] == {"paragraph": 2}
