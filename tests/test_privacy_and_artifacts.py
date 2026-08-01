from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1].resolve()


def test_no_private_paths_or_secrets_in_tracked_files() -> None:
    user_name = os.environ.get("USER", "")
    if (
        not user_name
        or len(user_name) < 3
        or user_name.lower() in {"runner", "root", "ubuntu", "user", "runneradmin"}
    ):
        return

    tracked_files = [
        path
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
        and ".mypy_cache" not in path.parts
        and ".pytest_cache" not in path.parts
        and ".ruff_cache" not in path.parts
        and not path.name.endswith(".pyc")
        and path.name != "test_privacy_and_artifacts.py"
    ]

    for file_path in tracked_files:
        if file_path.suffix in {".docx", ".pdf", ".png", ".wav", ".xlsx", ".pptx", ".zip"}:
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        assert user_name not in content, (
            f"File {file_path.relative_to(REPO_ROOT)} contains username: {user_name}"
        )


def test_acceptance_report_has_no_private_host_data(tmp_path: Path) -> None:
    accept_script = REPO_ROOT / "scripts" / "accept_host.py"
    report_file = tmp_path / "acceptance-report.json"

    subprocess.run(
        [sys.executable, str(accept_script), "--output", str(report_file)],
        cwd=REPO_ROOT,
        check=True,
    )

    assert report_file.is_file()
    data = json.loads(report_file.read_text(encoding="utf-8"))

    assert data["schema_version"] == "1"
    assert data["overall_status"] == "succeeded"

    report_str = json.dumps(data)
    assert "sk-" not in report_str
    assert "ghp_" not in report_str
    assert "bearer " not in report_str.lower()
