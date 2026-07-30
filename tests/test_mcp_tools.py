from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mcp_support import MCP_TEXT_FIXTURES
from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import Citation
from archiv.hashing import sha256_file
from archiv.mcp_tools import (
    archiv_generate_docx,
    archiv_get_run_evidence,
    archiv_ingest,
    archiv_read_source,
    archiv_search,
    archiv_verify_artifact,
)
from archiv.search import rebuild_search_index

runner = CliRunner()


def _search_results(envelope_result: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], envelope_result["results"])


def test_all_bounded_tools_return_evidence_and_preserve_sources(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "archiv-home"
    monkeypatch.setenv("ARCHIV_HOME", str(home))
    source_hashes = {
        ingestion_corpus / filename: sha256_file(ingestion_corpus / filename)
        for filename in MCP_TEXT_FIXTURES
    }

    ingestion_runs = [
        archiv_ingest(str(ingestion_corpus / filename)) for filename in MCP_TEXT_FIXTURES
    ]
    assert all(run.status == "succeeded" for run in ingestion_runs)
    rebuild_search_index(home=home)

    searched = archiv_search("MARKER", limit=10)
    results = _search_results(searched.result)
    assert len(results) == len(MCP_TEXT_FIXTURES)
    citation = Citation.model_validate(results[0]["citation"])

    read = archiv_read_source(citation)
    assert read.result["excerpt"] == results[0]["text"]

    generated = archiv_generate_docx("MARKER", "mcp-report.docx", max_sources=3)
    report = cast(dict[str, object], generated.result["report"])
    assert Path(cast(str, report["docx_path"])).is_file()

    verified = archiv_verify_artifact("mcp-report.docx")
    validation = cast(dict[str, object], verified.result["validation"])
    assert validation["valid"] is True

    evidence = archiv_get_run_evidence(searched.run_id)
    evidence_payload = cast(dict[str, object], evidence.result["evidence"])
    target_result = cast(dict[str, object], evidence_payload["target_result"])
    assert target_result["run_id"] == searched.run_id
    assert target_result["status"] == "succeeded"

    assert {path: sha256_file(path) for path in source_hashes} == source_hashes
    for run in [*ingestion_runs, searched, read, generated, verified, evidence]:
        run_dir = Path(run.evidence_dir)
        assert run_dir.joinpath("request.json").is_file()
        assert run_dir.joinpath("result.json").is_file()


def test_cli_and_mcp_search_have_equivalent_machine_results(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "archiv-home"
    monkeypatch.setenv("ARCHIV_HOME", str(home))
    for filename in MCP_TEXT_FIXTURES:
        archiv_ingest(str(ingestion_corpus / filename))
    rebuild_search_index(home=home)

    mcp_result = archiv_search("MARKER", limit=10)
    cli_result = runner.invoke(
        app,
        ["search", "MARKER", "--home", str(home), "--limit", "10"],
    )

    assert cli_result.exit_code == 0, cli_result.output
    assert json.loads(cli_result.output) == mcp_result.result["results"]


def test_forbidden_paths_fail_and_persist_failed_evidence(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "archiv-home"
    monkeypatch.setenv("ARCHIV_HOME", str(home))

    with pytest.raises(ValueError, match="must be absolute"):
        archiv_ingest("relative.txt")

    archiv_ingest(str(ingestion_corpus / "plain-text.txt"))
    rebuild_search_index(home=home)
    with pytest.raises(ValueError, match="must not contain directories"):
        archiv_generate_docx("MARKER", "../escaped.docx")

    result_files = sorted((home / "runs" / "mcp").glob("*/result.json"))
    failed = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in result_files
        if json.loads(path.read_text(encoding="utf-8"))["status"] == "failed"
    ]
    assert len(failed) == 2
    assert {item["tool"] for item in failed} == {"archiv_ingest", "archiv_generate_docx"}
    assert not tmp_path.joinpath("escaped.docx").exists()
