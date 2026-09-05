#!/usr/bin/env python3
"""Report which planned step is next by running each step's acceptance check.

Progress is *derived*, never declared. A step is complete only when every target in
its acceptance check passes right now. That makes a forgotten status line harmless
and a regression in finished work immediately visible: if an earlier step breaks,
the reported next step moves backwards to it.

Usage:
    python scripts/plan_status.py                # human summary
    python scripts/plan_status.py --json         # machine-readable
    python scripts/plan_status.py --step S04     # check one step only
    python scripts/plan_status.py --list         # the queue, without running anything
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "docs" / "plan" / "queue.json"

Outcome = Literal["complete", "incomplete", "unknown"]


@dataclass(frozen=True, slots=True)
class Step:
    """One queued unit of work and the check that decides it is finished."""

    id: str
    phase: str
    title: str
    doc: str
    detail: str
    depends_on: tuple[str, ...]
    acceptance_kind: str
    targets: tuple[str, ...]


@dataclass
class StepResult:
    """Evaluated state of one step."""

    step: Step
    outcome: Outcome
    passing: list[str] = field(default_factory=lambda: list[str]())
    failing: list[str] = field(default_factory=lambda: list[str]())
    note: str = ""


class PlanError(RuntimeError):
    """The queue file is missing or malformed."""


def load_queue(path: Path = QUEUE_PATH) -> tuple[dict[str, Any], list[Step]]:
    """Load and shallow-validate the queue, returning the raw payload and its steps."""

    if not path.is_file():
        raise PlanError(f"queue file not found: {path}")
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise PlanError(f"queue file is not valid JSON: {error}") from error

    if payload.get("schema_version") != "1":
        raise PlanError('unsupported queue schema_version; expected "1"')

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("queue file declares no steps")

    known_kinds = set(payload.get("acceptance_kinds", {}))
    steps: list[Step] = []
    seen: set[str] = set()
    for entry in raw_steps:
        if not isinstance(entry, dict):
            raise PlanError("every step must be an object")
        step_id = str(entry.get("id", ""))
        if not step_id:
            raise PlanError("every step needs an id")
        if step_id in seen:
            raise PlanError(f"duplicate step id: {step_id}")
        seen.add(step_id)

        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, dict):
            raise PlanError(f"{step_id}: missing acceptance block")
        kind = str(acceptance.get("kind", ""))
        if known_kinds and kind not in known_kinds:
            raise PlanError(f"{step_id}: unknown acceptance kind {kind!r}")
        targets = acceptance.get("targets")
        if not isinstance(targets, list) or not targets:
            raise PlanError(f"{step_id}: acceptance declares no targets")

        depends = entry.get("depends_on", [])
        if not isinstance(depends, list):
            raise PlanError(f"{step_id}: depends_on must be a list")

        steps.append(
            Step(
                id=step_id,
                phase=str(entry.get("phase", "")),
                title=str(entry.get("title", "")),
                doc=str(entry.get("doc", "")),
                detail=str(entry.get("detail", "stub")),
                depends_on=tuple(str(item) for item in depends),
                acceptance_kind=kind,
                targets=tuple(str(target) for target in targets),
            )
        )

    for step in steps:
        for dependency in step.depends_on:
            if dependency not in seen:
                raise PlanError(f"{step.id}: depends on unknown step {dependency}")

    return payload, steps


def _pytest_outcomes(targets: list[str]) -> dict[str, bool]:
    """Run every pytest target in one invocation and map node id to pass/fail.

    A single invocation keeps this tool fast enough to run at the start of every
    session, however many steps are already finished. A target whose file does not
    exist yet, or whose node id is unknown, counts as not-passing.
    """

    if not targets:
        return {}

    # pytest exits with a usage error, running nothing at all, if any argument names a
    # missing file *or* a node id that does not exist yet. Both are normal here: a step
    # that has not been done yet has neither. So ask pytest only for the distinct files
    # that exist, let it report every test in them, and match node ids against the
    # results afterwards. A target that never appears simply is not passing.
    outcomes: dict[str, bool] = dict.fromkeys(targets, False)
    files = sorted({target.split("::", 1)[0] for target in targets})
    runnable = [name for name in files if (ROOT / name).is_file()]

    if not runnable:
        return outcomes

    with tempfile.TemporaryDirectory(prefix="archiv-plan-status-") as directory:
        report = Path(directory) / "report.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--continue-on-collection-errors",
            f"--junitxml={report}",
            *runnable,
        ]
        subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if not report.is_file():
            return outcomes
        try:
            tree = ElementTree.parse(report)
        except ElementTree.ParseError:
            return outcomes

    passed: set[str] = set()
    for case in tree.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        if not name:
            continue
        failed = any(child.tag in {"failure", "error", "skipped"} for child in case)
        if failed:
            continue
        path = classname.replace(".", "/")
        passed.add(f"{path}.py::{name}")
        passed.add(name)

    for target in targets:
        node = target.rsplit("::", 1)[-1]
        outcomes[target] = target in passed or node in passed
    return outcomes


def _artifact_outcomes(targets: list[str]) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for target in targets:
        path = ROOT / target
        if not path.is_file():
            outcomes[target] = False
            continue
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                outcomes[target] = False
                continue
        outcomes[target] = True
    return outcomes


def _command_outcomes(targets: list[str]) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for target in targets:
        completed = subprocess.run(
            target,
            cwd=ROOT,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        outcomes[target] = completed.returncode == 0
    return outcomes


def evaluate(steps: list[Step], *, only: str | None = None) -> list[StepResult]:
    """Evaluate every step (or one) and return results in queue order."""

    selected = [step for step in steps if only is None or step.id == only]
    if only is not None and not selected:
        raise PlanError(f"unknown step: {only}")

    by_kind: dict[str, list[str]] = {}
    for step in selected:
        by_kind.setdefault(step.acceptance_kind, []).extend(step.targets)

    outcomes: dict[str, bool] = {}
    if by_kind.get("pytest"):
        outcomes.update(_pytest_outcomes(by_kind["pytest"]))
    if by_kind.get("artifact"):
        outcomes.update(_artifact_outcomes(by_kind["artifact"]))
    if by_kind.get("command"):
        outcomes.update(_command_outcomes(by_kind["command"]))

    results: list[StepResult] = []
    for step in selected:
        passing = [target for target in step.targets if outcomes.get(target, False)]
        failing = [target for target in step.targets if not outcomes.get(target, False)]
        outcome: Outcome = "complete" if not failing else "incomplete"
        note = ""
        if outcome == "incomplete" and step.detail == "stub":
            note = f"expand {step.doc} before implementing — it is a stub"
        results.append(
            StepResult(step=step, outcome=outcome, passing=passing, failing=failing, note=note)
        )
    return results


def next_step(results: list[StepResult]) -> StepResult | None:
    """Return the first step that is not complete."""

    for result in results:
        if result.outcome != "complete":
            return result
    return None


def _payload(queue: dict[str, Any], results: list[StepResult]) -> dict[str, Any]:
    upcoming = next_step(results)
    complete = [result for result in results if result.outcome == "complete"]
    return {
        "schema_version": "1",
        "plan_id": queue.get("plan_id", ""),
        "steps_total": len(results),
        "steps_complete": len(complete),
        "next_step": None
        if upcoming is None
        else {
            "id": upcoming.step.id,
            "phase": upcoming.step.phase,
            "title": upcoming.step.title,
            "doc": upcoming.step.doc,
            "detail": upcoming.step.detail,
            "failing_targets": upcoming.failing,
            "note": upcoming.note,
        },
        "steps": [
            {
                "id": result.step.id,
                "phase": result.step.phase,
                "title": result.step.title,
                "outcome": result.outcome,
                "passing": len(result.passing),
                "targets": len(result.step.targets),
            }
            for result in results
        ],
    }


def _phase_titles(queue: dict[str, Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for phase in queue.get("phases", []):
        if isinstance(phase, dict):
            titles[str(phase.get("id", ""))] = str(phase.get("title", ""))
    return titles


def _render(queue: dict[str, Any], results: list[StepResult]) -> str:
    titles = _phase_titles(queue)
    lines: list[str] = []
    complete = sum(1 for result in results if result.outcome == "complete")
    lines.append(
        f"Archiv plan {queue.get('plan_id', '')} — {complete}/{len(results)} steps complete"
    )
    lines.append("")

    current_phase = ""
    for result in results:
        if result.step.phase != current_phase:
            current_phase = result.step.phase
            heading = titles.get(current_phase, current_phase)
            lines.append(f"  Phase {current_phase} — {heading}")
        mark = "done" if result.outcome == "complete" else "    "
        lines.append(f"    [{mark}] {result.step.id}  {result.step.title}")
    lines.append("")

    upcoming = next_step(results)
    if upcoming is None:
        lines.append("Every step is complete. Nothing is queued.")
        return "\n".join(lines)

    lines.append(f"Next step: {upcoming.step.id} — {upcoming.step.title}")
    lines.append(f"  Read: {upcoming.step.doc}")
    if upcoming.note:
        lines.append(f"  Note: {upcoming.note}")
    lines.append("  Not yet passing:")
    for target in upcoming.failing:
        lines.append(f"    - {target}")
    lines.append("")
    lines.append("Do only this step, then run the checks in CLAUDE.md and open one pull request.")
    return "\n".join(lines)


def _render_list(queue: dict[str, Any], steps: list[Step]) -> str:
    titles = _phase_titles(queue)
    lines: list[str] = []
    current_phase = ""
    for step in steps:
        if step.phase != current_phase:
            current_phase = step.phase
            lines.append(f"Phase {current_phase} — {titles.get(current_phase, current_phase)}")
        suffix = "  (stub)" if step.detail == "stub" else ""
        lines.append(f"  {step.id}  {step.title}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--step", default=None, help="Check only this step id, e.g. S04.")
    parser.add_argument(
        "--list", action="store_true", help="Print the queue without running checks."
    )
    parser.add_argument("--queue", default=None, help="Path to an alternate queue.json.")
    args = parser.parse_args(argv)

    queue_path = Path(args.queue) if args.queue else QUEUE_PATH
    try:
        queue, steps = load_queue(queue_path)
    except PlanError as error:
        print(f"plan status failed: {error}", file=sys.stderr)
        return 2

    if args.list:
        print(_render_list(queue, steps))
        return 0

    if shutil.which(sys.executable) is None:  # pragma: no cover - defensive
        print("plan status failed: no usable interpreter", file=sys.stderr)
        return 2

    try:
        results = evaluate(steps, only=args.step)
    except PlanError as error:
        print(f"plan status failed: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_payload(queue, results), indent=2, sort_keys=True))
    else:
        print(_render(queue, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
