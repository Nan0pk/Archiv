"""Direct deterministic implementation of Archiv's first execution contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from archiv.contracts import (
    FileChanges,
    RunResult,
    RunStatus,
    SourceHashes,
    SourceMarkerRequest,
    ValidationReport,
)
from archiv.hashing import file_evidence
from archiv.validators.source_marker import validate_source_marker


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _extract_marker(source_bytes: bytes) -> str:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("source.txt must be UTF-8") from error

    lines = text.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise ValueError("source.txt must contain exactly one non-empty marker line")
    return lines[0]


def run_source_marker(workspace: Path) -> RunResult:
    """Run the exact two-line file task and persist independent evidence."""

    workspace = workspace.expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be a directory")

    run_id = uuid4().hex
    evidence_dir = workspace / "runs" / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)

    request = SourceMarkerRequest(workspace=str(workspace))
    _write_json(evidence_dir / "request.json", request.model_dump(mode="json"))

    source_path = workspace / request.source_path
    output_path = workspace / request.output_path
    output_existed_before = output_path.exists()

    try:
        source_before = file_evidence(source_path, display_path=request.source_path)
        source_bytes = source_path.read_bytes()
        marker = _extract_marker(source_bytes)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output_path.with_name(f".{output_path.name}.{run_id}.tmp")
        temporary_output.write_bytes(f"HARNESS_OK\n{marker}\n".encode())
        os.replace(temporary_output, output_path)

        validation = validate_source_marker(
            source_path=source_path,
            output_path=output_path,
            expected_marker=marker,
            source_hash_before=source_before.sha256,
        )
        source_after = (
            file_evidence(source_path, display_path=request.source_path)
            if source_path.is_file()
            else None
        )
        source_hashes = SourceHashes(before=source_before, after=source_after)
        file_changes = FileChanges(
            created=[] if output_existed_before else [request.output_path],
            replaced=[request.output_path] if output_existed_before else [],
            forbidden_changes=[] if source_after == source_before else [request.source_path],
        )
        status = (
            RunStatus.SUCCEEDED if validation.passed else RunStatus.PARTIALLY_PRODUCED_BUT_INVALID
        )
        result = RunResult(
            run_id=run_id,
            status=status,
            output_path=request.output_path,
            evidence_dir=str(evidence_dir),
            validation=validation,
        )
    except (OSError, ValueError) as error:
        validation = ValidationReport(passed=False, errors=[f"{type(error).__name__}: {error}"])
        result = RunResult(
            run_id=run_id,
            status=RunStatus.FAILED,
            output_path=request.output_path if output_path.exists() else None,
            evidence_dir=str(evidence_dir),
            validation=validation,
        )
        source_hashes = None
        file_changes = FileChanges()

    if source_hashes is not None:
        _write_json(evidence_dir / "source-hashes.json", source_hashes.model_dump(mode="json"))
    _write_json(evidence_dir / "file-changes.json", file_changes.model_dump(mode="json"))
    _write_json(evidence_dir / "validation.json", validation.model_dump(mode="json"))
    _write_json(evidence_dir / "result.json", result.model_dump(mode="json"))
    return result
