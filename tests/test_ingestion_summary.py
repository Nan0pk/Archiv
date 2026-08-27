from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from archiv.cli import app
from archiv.ingestion.summary import IngestionCounts, validate_summary, write_summary


def test_summary_contract_is_aggregate_only_and_private(tmp_path: Path) -> None:
    output = write_summary(
        tmp_path / "export.json",
        IngestionCounts(supported=3, rejected=2, skipped=1, degraded=1, failed=0),
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload == {
        "schema_version": "1",
        "privacy": "aggregate_counts_only",
        "local_only": True,
        "counts": {
            "supported": 3,
            "rejected": 2,
            "skipped": 1,
            "degraded": 1,
            "failed": 0,
        },
    }
    assert output.stat().st_mode & 0o777 == 0o600
    assert validate_summary(output).counts.supported == 3


def test_summary_rejects_identifying_or_content_fields(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "privacy": "aggregate_counts_only",
                "local_only": True,
                "counts": IngestionCounts().model_dump(),
                "filenames": ["private.txt"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        validate_summary(path)


def test_add_exports_counts_without_source_identifiers(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "document.txt").write_text("lawful generated fixture\n", encoding="utf-8")
    (source / "secret.unsupported").write_text("not exported", encoding="utf-8")
    summary = tmp_path / "summary.json"

    result = CliRunner().invoke(
        app,
        ["add", str(source), "--home", str(tmp_path / "home"), "--summary-out", str(summary)],
    )

    assert result.exit_code == 0, result.output
    exported = summary.read_text(encoding="utf-8")
    assert validate_summary(summary).counts == IngestionCounts(supported=1, rejected=1)
    assert "document" not in exported
    assert "secret" not in exported


def test_add_exports_rejections_even_when_nothing_is_ingested(tmp_path: Path) -> None:
    source = tmp_path / "private.doc"
    source.write_bytes(b"generated unsupported fixture")
    summary = tmp_path / "summary.json"

    result = CliRunner().invoke(app, ["add", str(source), "--summary-out", str(summary)])

    assert result.exit_code == 1
    assert validate_summary(summary).counts == IngestionCounts(rejected=1)
    assert "private.doc" not in summary.read_text(encoding="utf-8")


def test_add_exports_malformed_failures_even_when_nothing_is_ingested(tmp_path: Path) -> None:
    source = tmp_path / "malformed.txt"
    source.write_bytes(b"\xff\xfe")
    summary = tmp_path / "summary.json"

    result = CliRunner().invoke(app, ["add", str(source), "--summary-out", str(summary)])

    assert result.exit_code == 1
    assert validate_summary(summary).counts == IngestionCounts(failed=1)
    assert "malformed.txt" not in summary.read_text(encoding="utf-8")
