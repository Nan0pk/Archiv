from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parents[1]
AUDITOR = ROOT / "scripts" / "audit_ci_trust.py"
PYTHON_SCRIPT_INVOCATION = re.compile(r"python3?\s+(scripts/[\w./-]+\.py)")


def _run_audit(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDITOR), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_repository_workflows_satisfy_public_pr_trust_boundary() -> None:
    completed = _run_audit(ROOT)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = cast(dict[str, object], json.loads(completed.stdout))
    assert payload["ok"] is True
    assert cast(int, payload["workflow_count"]) >= 6
    assert payload["violations"] == []


def test_auditor_rejects_untrusted_workflow_patterns(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    workflows.joinpath("unsafe.yml").write_text(
        """name: Unsafe
on:
  pull_request_target:
  pull_request:
permissions:
  contents: write
jobs:
  unsafe:
    runs-on: [self-hosted, linux]
    steps:
      - uses: actions/checkout@v4
      - run: echo '${{ secrets.DANGEROUS }}'
""",
        encoding="utf-8",
    )

    completed = _run_audit(tmp_path)
    assert completed.returncode == 1
    payload = cast(dict[str, object], json.loads(completed.stdout))
    violations = cast(list[dict[str, str]], payload["violations"])
    codes = {item["code"] for item in violations}
    assert {
        "pull_request_target",
        "pr_secret_reference",
        "pr_write_permission",
        "missing_pr_cancellation",
        "untrusted_self_hosted",
        "unpinned_action",
        "checkout_credentials",
    } <= codes


def test_governance_document_records_solo_maintainer_safe_settings() -> None:
    governance = ROOT.joinpath("docs", "github-governance.md").read_text(encoding="utf-8")
    for required in (
        "squash merge",
        "force pushes",
        "Fast checks / quality",
        "zero required approving reviews",
        "private vulnerability reporting",
        "secret scanning",
        "push protection",
        "GitHub-hosted",
        "manual owner action",
    ):
        assert required in governance


def _scripts_ci_runs() -> set[str]:
    """Every ``scripts/*.py`` path a workflow's own ``run:`` step invokes directly."""

    workflows_dir = ROOT / ".github" / "workflows"
    referenced: set[str] = set()
    for workflow in sorted({*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")}):
        text = workflow.read_text(encoding="utf-8")
        referenced.update(PYTHON_SCRIPT_INVOCATION.findall(text))
    return referenced


def test_every_script_ci_runs_is_type_checked() -> None:
    """A script a workflow invokes must fall under pyright's own `include` list.

    `scripts/build_office_validation_artifacts.py` once called `generate_report` with
    its old signature after that function gained two required parameters. Every local
    check passed because `pyright`'s `include` list only covered `src` and `tests`;
    `scripts` was merely on `extraPaths`, which makes it importable but not checked.
    The failure would only have surfaced in a job that needs LibreOffice and Poppler
    and cannot run in the standard container. This test keeps that gap from reopening:
    it fails the moment a workflow gains a script invocation `pyright` does not cover.
    """

    referenced = _scripts_ci_runs()
    assert referenced, "expected at least one workflow to invoke a scripts/*.py file"

    pyright_config = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    include = cast(list[str], pyright_config["tool"]["pyright"]["include"])

    uncovered = {
        path
        for path in referenced
        if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in include)
    }
    assert not uncovered, (
        "these scripts are invoked by CI but not covered by pyright's include list "
        f"in pyproject.toml: {sorted(uncovered)}"
    )


def test_security_workflows_use_trusted_events_and_pinned_actions() -> None:
    dependency_review = ROOT.joinpath(".github", "workflows", "dependency-review.yml").read_text(
        encoding="utf-8"
    )
    codeql = ROOT.joinpath(".github", "workflows", "codeql.yml").read_text(encoding="utf-8")

    assert "pull_request:" in dependency_review
    assert "contents: read" in dependency_review
    assert "fail-on-severity: high" in dependency_review
    assert "pull_request_target" not in dependency_review

    assert "push:" in codeql
    assert "schedule:" in codeql
    assert "pull_request:" not in codeql
    assert "security-events: write" in codeql
    assert "github/codeql-action/" in codeql
