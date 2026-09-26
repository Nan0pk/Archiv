from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from typer.main import get_command

from archiv.cli import app

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
AUDITOR = ROOT / "scripts" / "audit_status_claims.py"


def _leaf_command_paths() -> list[tuple[str, ...]]:
    """Every command path the CLI actually registers, including nested groups."""

    paths: list[tuple[str, ...]] = []

    def walk(command: object, prefix: tuple[str, ...]) -> None:
        subcommands = getattr(command, "commands", None)
        if subcommands:
            for name, sub in subcommands.items():
                walk(sub, (*prefix, name))
        else:
            paths.append(prefix)

    walk(get_command(app), ())
    return paths


def test_every_registered_cli_command_appears_in_the_readme() -> None:
    text = README.read_text(encoding="utf-8")
    missing = [
        "archiv " + " ".join(path)
        for path in _leaf_command_paths()
        if "archiv " + " ".join(path) not in text
    ]
    assert not missing, f"registered but undocumented in README.md: {missing}"


def _run_audit(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDITOR), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_no_plan_status_line_claims_done_without_its_artefact() -> None:
    completed = _run_audit(ROOT)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = cast(dict[str, object], json.loads(completed.stdout))
    assert payload["ok"] is True
    assert payload["violations"] == []


def test_auditor_rejects_a_completion_claim_with_no_artefact(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    docs.joinpath("plan.md").write_text(
        "# Plan\n\n"
        "> **Status: Implemented.** All milestones are done and verified with "
        "comprehensive acceptance tests.\n",
        encoding="utf-8",
    )
    completed = _run_audit(tmp_path)
    assert completed.returncode == 1
    payload = cast(dict[str, object], json.loads(completed.stdout))
    assert payload["ok"] is False
    violations = cast(list[dict[str, object]], payload["violations"])
    assert violations[0]["doc"] == "docs/plan.md"


def test_auditor_accepts_a_completion_claim_backed_by_a_test_path(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    docs.joinpath("plan.md").write_text(
        "# Plan\n\n> **Status: Implemented.** Proven by `tests/test_plan.py::test_it_works`.\n",
        encoding="utf-8",
    )
    completed = _run_audit(tmp_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
