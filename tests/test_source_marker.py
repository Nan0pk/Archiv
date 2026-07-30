from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import RunStatus
from archiv.executor.source_marker import run_source_marker
from archiv.hashing import file_evidence
from archiv.validators.source_marker import validate_source_marker

runner = CliRunner()


def _workspace(tmp_path: Path, marker: str = "ARCHIV-MARKER-123") -> Path:
    tmp_path.joinpath("source.txt").write_text(f"{marker}\n", encoding="utf-8")
    return tmp_path


def test_source_marker_succeeds_with_exact_bytes_and_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_before = workspace.joinpath("source.txt").read_bytes()

    result = run_source_marker(workspace)

    assert result.status is RunStatus.SUCCEEDED
    assert workspace.joinpath("outputs/probe.txt").read_bytes() == (
        b"HARNESS_OK\nARCHIV-MARKER-123\n"
    )
    assert workspace.joinpath("source.txt").read_bytes() == source_before

    evidence_dir = Path(result.evidence_dir)
    assert {path.name for path in evidence_dir.iterdir()} == {
        "file-changes.json",
        "request.json",
        "result.json",
        "source-hashes.json",
        "validation.json",
    }
    recorded = json.loads(evidence_dir.joinpath("result.json").read_text(encoding="utf-8"))
    assert recorded["status"] == "succeeded"
    assert recorded["validation"]["passed"] is True


def test_validator_rejects_extra_blank_line(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_hash = file_evidence(workspace / "source.txt").sha256
    output = workspace / "outputs/probe.txt"
    output.parent.mkdir()
    output.write_bytes(b"HARNESS_OK\nARCHIV-MARKER-123\n\n")

    validation = validate_source_marker(
        source_path=workspace / "source.txt",
        output_path=output,
        expected_marker="ARCHIV-MARKER-123",
        source_hash_before=source_hash,
    )

    assert validation.passed is False
    assert validation.errors == ["output_bytes_mismatch"]


def test_validator_rejects_source_mutation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_hash = file_evidence(workspace / "source.txt").sha256
    workspace.joinpath("outputs").mkdir()
    workspace.joinpath("outputs/probe.txt").write_bytes(
        b"HARNESS_OK\nARCHIV-MARKER-123\n"
    )
    workspace.joinpath("source.txt").write_text("MUTATED\n", encoding="utf-8")

    validation = validate_source_marker(
        source_path=workspace / "source.txt",
        output_path=workspace / "outputs/probe.txt",
        expected_marker="ARCHIV-MARKER-123",
        source_hash_before=source_hash,
    )

    assert validation.passed is False
    assert validation.errors == ["source_hash_changed"]


def test_validator_rejects_missing_output(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    source_hash = file_evidence(workspace / "source.txt").sha256

    validation = validate_source_marker(
        source_path=workspace / "source.txt",
        output_path=workspace / "outputs/probe.txt",
        expected_marker="ARCHIV-MARKER-123",
        source_hash_before=source_hash,
    )

    assert validation.passed is False
    assert validation.errors == ["required_output_missing"]


def test_invalid_source_records_failed_run(tmp_path: Path) -> None:
    tmp_path.joinpath("source.txt").write_text("one\ntwo\n", encoding="utf-8")

    result = run_source_marker(tmp_path)

    assert result.status is RunStatus.FAILED
    assert result.validation.passed is False
    assert not tmp_path.joinpath("outputs/probe.txt").exists()
    assert Path(result.evidence_dir, "result.json").is_file()


def test_cli_emits_machine_readable_success(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = runner.invoke(app, ["source-marker", "--workspace", str(workspace)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["validation"]["passed"] is True
