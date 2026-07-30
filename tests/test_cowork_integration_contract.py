from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from archiv.cowork_contracts import (
    CoworkFaultDomain,
    CoworkRegressionReport,
    CoworkStageResult,
    CoworkStageStatus,
)

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "integrations" / "cowork-os"


def _json(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def test_upstream_lock_is_full_immutable_and_never_auto_adopted() -> None:
    lock = _json(INTEGRATION / "upstream-lock.json")
    revision = cast(str, lock["revision"])
    source_blobs = cast(dict[str, str], lock["source_blobs"])

    assert lock["repository"] == "CoWork-OS/CoWork-OS"
    assert len(revision) == 40
    assert lock["version"] == "0.5.50"
    assert lock["protocol_version"] == "2024-11-05"
    assert "manual pull request only" in cast(str, lock["adoption_policy"])
    assert len(source_blobs) >= 8
    assert all(len(blob) == 40 for blob in source_blobs.values())


def test_example_config_is_local_stdio_with_fixed_archive_home() -> None:
    config = _json(INTEGRATION / "mcp-server.example.json")

    assert config["transport"] == "stdio"
    assert config["command"] == "archiv-mcp"
    assert config["args"] == []
    assert "url" not in config
    env = cast(dict[str, str], config["env"])
    assert env == {"ARCHIV_HOME": "/absolute/path/to/archiv-home"}
    assert cast(str, config["cwd"]).startswith("/absolute/path/")


def test_operator_skill_requires_validator_and_run_evidence() -> None:
    skill = INTEGRATION.joinpath("SKILL.md").read_text(encoding="utf-8")

    for tool in (
        "archiv_ingest",
        "archiv_search",
        "archiv_read_source",
        "archiv_generate_docx",
        "archiv_verify_artifact",
        "archiv_get_run_evidence",
    ):
        assert tool in skill
    assert "validation.valid: true" in skill
    assert "Never infer success from assistant prose" in skill
    assert "Do not use shell tools" in skill


def test_regression_workflow_separates_pinned_and_current_upstream() -> None:
    workflow = ROOT.joinpath(
        ".github", "workflows", "cowork-upstream-regression.yml"
    ).read_text(encoding="utf-8")

    assert "mode: pinned" in workflow
    assert "mode: current" in workflow
    assert "8c01f1271722a5bb5b8d68ee76b8c68353a564bd" in workflow
    assert "revision: main" in workflow
    assert "npx --yes tsx@4.20.6" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "upstream-lock.json" in workflow
    assert "update" not in workflow.lower().split("adoption", 1)[0]


def test_regression_contract_has_explicit_fault_ownership() -> None:
    stages = [
        CoworkStageResult(
            stage="transport",
            status=CoworkStageStatus.PASSED,
            owner_if_failed=CoworkFaultDomain.MCP_TRANSPORT,
            detail="passed",
        ),
        CoworkStageResult(
            stage="model_provider",
            status=CoworkStageStatus.NOT_EXERCISED,
            owner_if_failed=CoworkFaultDomain.MODEL_PROVIDER,
            detail="not required for deterministic compatibility",
        ),
    ]
    report = CoworkRegressionReport(
        mode="pinned",
        cowork_revision="8c01f1271722a5bb5b8d68ee76b8c68353a564bd",
        locked_revision="8c01f1271722a5bb5b8d68ee76b8c68353a564bd",
        cowork_version="0.5.50",
        compatible=True,
        source_hashes_unchanged=True,
        exact_task_succeeded=True,
        valid_report_path="/tmp/report.docx",
        evidence_run_ids=["0" * 32],
        stages=stages,
    )

    assert report.upstream_adopted is False
    assert report.stages[0].owner_if_failed is CoworkFaultDomain.MCP_TRANSPORT
    assert report.stages[1].status is CoworkStageStatus.NOT_EXERCISED


def test_probe_uses_actual_cowork_transport_and_checks_false_success() -> None:
    probe = ROOT.joinpath("scripts", "cowork_stdio_probe.ts").read_text(encoding="utf-8")

    assert "src/electron/mcp/client/transports/StdioTransport.ts" in probe
    assert 'protocolVersion: "2024-11-05"' in probe
    assert 'transport.sendRequest("tools/list")' in probe
    assert "failed_validation_cannot_succeed" in probe
    assert "result.isError !== true" in probe
