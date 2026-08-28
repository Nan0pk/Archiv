from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from privacy_scan_support import files_containing

REPO_ROOT = Path(__file__).parents[1].resolve()


def test_no_private_paths_or_secrets_in_tracked_files() -> None:
    """No git-tracked file may contain this contributor's actual home directory.

    Matches the full home-directory path, not a bare username token: a token match
    is both too broad (a username that happens to spell an ordinary word -- "victus"
    collides with the HP Victus hardware this project documents on purpose -- makes
    the check permanently unpassable for that contributor) and too narrow (it never
    runs on hosted CI, whose account name is always in a fixed skip-list). A full
    path match has neither problem: it can only ever match a real leaked path, and
    a hosted runner's own home (e.g. /home/runner) is exactly as real a leak there
    as this contributor's is here, so there is nothing to skip.
    """

    home = str(Path.home())
    if home in {"", "/"}:  # a broken environment with no real home; nothing to check
        return

    hits = files_containing(REPO_ROOT, home, exclude=Path(__file__).resolve())
    assert not hits, (
        f"{len(hits)} tracked file(s) contain this machine's home directory ({home}): "
        + ", ".join(str(path.relative_to(REPO_ROOT)) for path in hits)
    )


def test_acceptance_report_has_no_private_host_data(tmp_path: Path) -> None:
    accept_script = REPO_ROOT / "scripts" / "accept_host.py"
    report_file = tmp_path / "acceptance-report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"

    proc = subprocess.run(
        [sys.executable, str(accept_script), "--output", str(report_file)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    report_detail = ""
    if report_file.is_file():
        report_detail = f"\nREPORT:\n{report_file.read_text(encoding='utf-8')}"
    assert proc.returncode == 0, (
        f"accept_host.py failed:{report_detail}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )

    assert report_file.is_file()
    data = json.loads(report_file.read_text(encoding="utf-8"))

    assert data["schema_version"] == "1"
    assert data["overall_status"] == "succeeded"

    report_str = json.dumps(data)
    assert "sk-" not in report_str
    assert "ghp_" not in report_str
    assert "bearer " not in report_str.lower()
