#!/usr/bin/env python3
"""Combine CoWork source, transport, Archiv, and evidence regression results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from archiv.cowork_contracts import (
    CoworkFaultDomain,
    CoworkRegressionReport,
    CoworkStageResult,
    CoworkStageStatus,
)
from archiv.hashing import sha256_file
from archiv.reports import validate_report


def _json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--mode", choices=("pinned", "current"), required=True)
    arguments = parser.parse_args()

    output_dir = arguments.output_dir.resolve()
    plan = _json(output_dir / "regression-plan.json")
    source_report = _json(arguments.source_report)
    probe = _json(output_dir / "cowork-probe.json")
    lock = _json(arguments.lock)
    home = Path(cast(str, plan["archiv_home"]))
    stages: list[CoworkStageResult] = []

    source_checks = cast(list[dict[str, object]], source_report["checks"])
    for check in source_checks:
        passed = cast(bool, check["passed"])
        stages.append(
            CoworkStageResult(
                stage="cowork_source:" + cast(str, check["name"]),
                status=CoworkStageStatus.PASSED if passed else CoworkStageStatus.FAILED,
                owner_if_failed=CoworkFaultDomain.COWORK_INTEGRATION,
                detail=cast(str, check["detail"]),
            )
        )

    probe_stages = cast(list[dict[str, object]], probe["stages"])
    stages.extend(CoworkStageResult.model_validate(stage) for stage in probe_stages)

    source_hashes = cast(dict[str, str], plan["source_hashes"])
    source_hashes_unchanged = all(
        Path(source).is_file() and sha256_file(Path(source)) == digest
        for source, digest in source_hashes.items()
    )
    stages.append(
        CoworkStageResult(
            stage="source_hashes_unchanged",
            status=(
                CoworkStageStatus.PASSED if source_hashes_unchanged else CoworkStageStatus.FAILED
            ),
            owner_if_failed=CoworkFaultDomain.ARCHIV,
            detail="Synthetic input files remained byte-for-byte unchanged.",
        )
    )

    exact = cast(dict[str, object], plan["exact_task"])
    exact_source = Path(cast(str, exact["workspace"])) / "source.txt"
    exact_output = Path(cast(str, exact["output_path"]))
    exact_task_succeeded = (
        exact["status"] == "succeeded"
        and exact_source.is_file()
        and sha256_file(exact_source) == exact["source_hash"]
        and exact_output.is_file()
        and exact_output.read_text(encoding="utf-8") == exact["expected_output"]
    )
    stages.append(
        CoworkStageResult(
            stage="exact_source_marker",
            status=(CoworkStageStatus.PASSED if exact_task_succeeded else CoworkStageStatus.FAILED),
            owner_if_failed=CoworkFaultDomain.ARCHIV,
            detail="The exact two-line task retained its source hash and external validation.",
        )
    )

    valid_report_path = Path(cast(str, probe["valid_report_path"]))
    valid_manifest_path = Path(cast(str, probe["valid_manifest_path"]))
    report_validation = validate_report(
        valid_report_path,
        valid_manifest_path,
        home=home,
        render=False,
    )
    stages.append(
        CoworkStageResult(
            stage="valid_report_outside_cowork",
            status=(
                CoworkStageStatus.PASSED if report_validation.valid else CoworkStageStatus.FAILED
            ),
            owner_if_failed=CoworkFaultDomain.ARCHIV,
            detail=(
                "The cited DOCX revalidated outside CoWork."
                if report_validation.valid
                else "; ".join(report_validation.errors)
            ),
        )
    )

    run_ids = cast(list[str], probe["run_ids"])
    evidence_accessible = bool(run_ids) and all(
        home.joinpath("runs", "mcp", run_id, "request.json").is_file()
        and home.joinpath("runs", "mcp", run_id, "result.json").is_file()
        for run_id in run_ids
    )
    stages.append(
        CoworkStageResult(
            stage="run_evidence_files_accessible",
            status=(CoworkStageStatus.PASSED if evidence_accessible else CoworkStageStatus.FAILED),
            owner_if_failed=CoworkFaultDomain.ARCHIV,
            detail="MCP request and terminal result records are readable without CoWork.",
        )
    )

    compatible = (
        cast(bool, source_report["compatible"])
        and source_hashes_unchanged
        and exact_task_succeeded
        and report_validation.valid
        and evidence_accessible
        and all(stage.status is not CoworkStageStatus.FAILED for stage in stages)
    )
    report = CoworkRegressionReport(
        mode=cast(str, arguments.mode),
        cowork_revision=cast(str, source_report["cowork_revision"]),
        locked_revision=cast(str, lock["revision"]),
        cowork_version=cast(str, source_report["cowork_version"]),
        compatible=compatible,
        source_hashes_unchanged=source_hashes_unchanged,
        exact_task_succeeded=exact_task_succeeded,
        valid_report_path=str(valid_report_path),
        evidence_run_ids=run_ids,
        stages=stages,
    )
    output_dir.joinpath("regression-report.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not compatible:
        failed = [stage.stage for stage in stages if stage.status is CoworkStageStatus.FAILED]
        raise SystemExit("CoWork regression failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
