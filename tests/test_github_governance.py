from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

ROOT = Path(__file__).parents[1]
AUDITOR = ROOT / "scripts" / "audit_ci_trust.py"


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
