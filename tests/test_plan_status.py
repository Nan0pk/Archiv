"""Tests for the plan queue and the status tool that derives progress from it.

The guardrail needs its own proof that it enforces something, not merely that the
current tree happens to pass -- the same principle stated in
``tests/privacy_scan_support.py``. So these tests build synthetic queues with known
outcomes rather than only asserting against the real one.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "plan_status.py"
QUEUE_PATH = ROOT / "docs" / "plan" / "queue.json"
QUEUE_MARKDOWN = ROOT / "docs" / "plan" / "QUEUE.md"

sys.path.insert(0, str(ROOT / "scripts"))

from plan_status import (  # noqa: E402
    PlanError,
    evaluate,
    load_queue,
    next_step,
)


def _write_queue(path: Path, steps: list[dict[str, Any]]) -> Path:
    payload: dict[str, Any] = {
        "schema_version": "1",
        "plan_id": "TEST-PLAN",
        "acceptance_kinds": {"artifact": "paths that must exist"},
        "phases": [{"id": "A", "title": "Test phase"}],
        "steps": steps,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _artifact_step(step_id: str, target: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "phase": "A",
        "title": f"Step {step_id}",
        "doc": f"docs/plan/steps/{step_id}.md",
        "detail": "full",
        "depends_on": [],
        "acceptance": {"kind": "artifact", "targets": [target]},
    }


def test_every_step_has_a_doc_and_a_declared_acceptance_check() -> None:
    payload, steps = load_queue(QUEUE_PATH)

    assert payload["schema_version"] == "1"
    assert steps, "the queue must declare at least one step"

    phase_ids = {str(phase["id"]) for phase in payload["phases"]}
    seen: set[str] = set()
    for step in steps:
        assert step.id not in seen, f"duplicate step id {step.id}"
        seen.add(step.id)
        assert step.phase in phase_ids, f"{step.id} names an unknown phase {step.phase}"
        assert step.title.strip(), f"{step.id} has no title"
        assert step.targets, f"{step.id} declares no acceptance targets"
        assert step.detail in {"full", "stub"}, f"{step.id} has an unknown detail level"

        doc = ROOT / step.doc
        assert doc.is_file(), f"{step.id} points at a missing doc: {step.doc}"
        body = doc.read_text(encoding="utf-8")
        assert body.startswith(f"# {step.id} "), f"{doc} must open with its step id"

    # Dependencies must be declared earlier in the queue, so the order is executable.
    position = {step.id: index for index, step in enumerate(steps)}
    for step in steps:
        for dependency in step.depends_on:
            assert position[dependency] < position[step.id], (
                f"{step.id} depends on {dependency}, which comes later in the queue"
            )


def test_queue_json_matches_queue_markdown() -> None:
    """The human-readable view may not drift from the machine-readable source."""

    _, steps = load_queue(QUEUE_PATH)
    markdown = QUEUE_MARKDOWN.read_text(encoding="utf-8")

    listed = re.findall(r"^\| \*\*(S\d+A?)\*\* \| (.+?) \|", markdown, flags=re.MULTILINE)
    assert listed, "QUEUE.md declares no steps"

    assert [step_id for step_id, _ in listed] == [step.id for step in steps], (
        "QUEUE.md lists different steps, or a different order, from queue.json"
    )
    for (step_id, title), step in zip(listed, steps, strict=True):
        assert title.strip() == step.title, f"{step_id} has a different title in QUEUE.md"


def test_status_tool_reports_first_incomplete_step(tmp_path: Path) -> None:
    done = tmp_path / "done.json"
    done.write_text("{}", encoding="utf-8")

    queue = _write_queue(
        tmp_path / "queue.json",
        [
            _artifact_step("S01", str(done)),
            _artifact_step("S02", str(tmp_path / "absent.json")),
            _artifact_step("S03", str(tmp_path / "also-absent.json")),
        ],
    )

    _, steps = load_queue(queue)
    results = evaluate(steps)

    assert [result.outcome for result in results] == ["complete", "incomplete", "incomplete"]
    upcoming = next_step(results)
    assert upcoming is not None
    assert upcoming.step.id == "S02"


def test_status_tool_detects_regression_in_a_completed_step(tmp_path: Path) -> None:
    """A queue that cannot notice finished work breaking is not self-verifying."""

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text("{}", encoding="utf-8")

    queue = _write_queue(
        tmp_path / "queue.json",
        [_artifact_step("S01", str(first)), _artifact_step("S02", str(second))],
    )
    _, steps = load_queue(queue)

    upcoming = next_step(evaluate(steps))
    assert upcoming is not None and upcoming.step.id == "S02"

    # Finish the second step, then break the first.
    second.write_text("{}", encoding="utf-8")
    assert next_step(evaluate(steps)) is None

    first.unlink()
    regressed = next_step(evaluate(steps))
    assert regressed is not None
    assert regressed.step.id == "S01", "the pointer must move backwards to the broken step"


def test_malformed_queue_is_rejected_rather_than_half_read(tmp_path: Path) -> None:
    duplicated = _write_queue(
        tmp_path / "duplicated.json",
        [_artifact_step("S01", "a"), _artifact_step("S01", "b")],
    )
    with pytest.raises(PlanError, match="duplicate step id"):
        load_queue(duplicated)

    missing_targets = tmp_path / "empty-targets.json"
    _write_queue(missing_targets, [_artifact_step("S01", "a")])
    payload = json.loads(missing_targets.read_text(encoding="utf-8"))
    payload["steps"][0]["acceptance"]["targets"] = []
    missing_targets.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanError, match="declares no targets"):
        load_queue(missing_targets)

    with pytest.raises(PlanError, match="not found"):
        load_queue(tmp_path / "absent.json")


def test_status_tool_runs_as_a_command_and_emits_json(tmp_path: Path) -> None:
    done = tmp_path / "done.json"
    done.write_text("{}", encoding="utf-8")
    queue = _write_queue(
        tmp_path / "queue.json",
        [_artifact_step("S01", str(done)), _artifact_step("S02", str(tmp_path / "absent.json"))],
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--queue", str(queue)],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = cast(dict[str, Any], json.loads(completed.stdout))

    assert payload["steps_total"] == 2
    assert payload["steps_complete"] == 1
    assert payload["next_step"]["id"] == "S02"
