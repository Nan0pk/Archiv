#!/usr/bin/env python3
"""Audit GitHub Actions workflows for Archiv's public-PR trust boundary."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

USES_PATTERN = re.compile(r"(?m)^\s*-?\s*uses:\s*([^@\s]+)@([^\s#]+)")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PULL_REQUEST_PATTERN = re.compile(r"(?m)^\s{0,4}pull_request\s*:")
PULL_REQUEST_TARGET_PATTERN = re.compile(r"(?m)^\s{0,4}pull_request_target\s*:")
WORKFLOW_DISPATCH_PATTERN = re.compile(r"(?m)^\s{0,4}workflow_dispatch\s*:")
RUNS_ON_PATTERN = re.compile(r"(?m)^\s*runs-on:\s*(.+)$")
WRITE_PERMISSION_PATTERN = re.compile(r"(?m)^\s+[a-zA-Z0-9_-]+:\s*write\s*$")


@dataclass(frozen=True, slots=True)
class Violation:
    workflow: str
    code: str
    detail: str


def _workflow_files(root: Path) -> list[Path]:
    workflows = root / ".github" / "workflows"
    return sorted({*workflows.glob("*.yml"), *workflows.glob("*.yaml")})


def _checkout_has_disabled_credentials(lines: list[str], index: int) -> bool:
    for candidate in lines[index + 1 :]:
        stripped = candidate.lstrip()
        if (
            stripped.startswith("- name:")
            or stripped.startswith("- uses:")
            or stripped.startswith("- run:")
        ):
            break
        if re.search(r"persist-credentials:\s*false\s*$", candidate):
            return True
    return False


def audit(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    workflows = _workflow_files(root)
    if not workflows:
        return [Violation(".github/workflows", "missing_workflows", "no workflow files found")]

    for workflow in workflows:
        relative = workflow.relative_to(root).as_posix()
        text = workflow.read_text(encoding="utf-8")
        lines = text.splitlines()
        handles_pr = bool(PULL_REQUEST_PATTERN.search(text))
        handles_pr_target = bool(PULL_REQUEST_TARGET_PATTERN.search(text))
        manual_only = bool(WORKFLOW_DISPATCH_PATTERN.search(text)) and not handles_pr

        if handles_pr_target:
            violations.append(
                Violation(
                    relative,
                    "pull_request_target",
                    "pull_request_target must not execute repository code",
                )
            )
        if "permissions:" not in text:
            violations.append(
                Violation(
                    relative,
                    "missing_permissions",
                    "workflow must declare explicit permissions",
                )
            )
        if handles_pr and "${{ secrets." in text:
            violations.append(
                Violation(
                    relative,
                    "pr_secret_reference",
                    "pull-request workflow references repository secrets",
                )
            )
        if handles_pr and WRITE_PERMISSION_PATTERN.search(text):
            violations.append(
                Violation(
                    relative,
                    "pr_write_permission",
                    "pull-request workflow requests a write permission",
                )
            )
        if handles_pr and "cancel-in-progress: true" not in text:
            violations.append(
                Violation(
                    relative,
                    "missing_pr_cancellation",
                    "pull-request workflow must cancel superseded runs",
                )
            )

        for match in RUNS_ON_PATTERN.finditer(text):
            runner = match.group(1).lower()
            if "self-hosted" in runner and not manual_only:
                violations.append(
                    Violation(
                        relative,
                        "untrusted_self_hosted",
                        (
                            "self-hosted runners are allowed only in manual workflows "
                            "with no pull-request trigger"
                        ),
                    )
                )

        for action, ref in USES_PATTERN.findall(text):
            if action.startswith("./"):
                continue
            if not FULL_SHA_PATTERN.fullmatch(ref):
                violations.append(
                    Violation(
                        relative,
                        "unpinned_action",
                        f"{action}@{ref} is not pinned to a full commit SHA",
                    )
                )

        for index, line in enumerate(lines):
            if (
                "uses: actions/checkout@" in line
                and not _checkout_has_disabled_credentials(lines, index)
            ):
                violations.append(
                    Violation(
                        relative,
                        "checkout_credentials",
                        "actions/checkout must set persist-credentials: false",
                    )
                )

    return violations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    root = arguments.root.resolve()
    violations = audit(root)
    payload = {
        "schema_version": "1",
        "ok": not violations,
        "workflow_count": len(_workflow_files(root)),
        "violations": [asdict(item) for item in violations],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if violations:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
