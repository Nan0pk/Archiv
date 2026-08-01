#!/usr/bin/env python3
"""Host acceptance script for Archiv alpha.

Executes end-to-end verification over public-safe fixtures or optional local folder,
recording only safe operational metadata.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).parents[1].resolve()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def git_commit_sha() -> str:
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return process.stdout.strip()
    except Exception:
        return "unknown-commit"


def get_platform_info() -> dict[str, object]:
    return {
        "os_system": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def run_cmd(args: list[str], *, cwd: Path | None = None) -> tuple[int, float, str, str]:
    start_time = time.monotonic()
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"
    result = subprocess.run(
        args,
        cwd=cwd or REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - start_time
    return result.returncode, round(duration, 4), result.stdout, result.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="Archiv host acceptance script")
    parser.add_argument("--user-folder", type=Path, help="Optional user folder to ingest")
    parser.add_argument("--output", type=Path, help="Output path for acceptance JSON report")
    parser.add_argument(
        "--local-only", action="store_true", help="Include verbose local debug info"
    )
    arguments = parser.parse_args()

    commit = git_commit_sha()
    plat_info = get_platform_info()

    report: dict[str, object] = {
        "schema_version": "1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_commit": commit,
        "platform": plat_info,
        "steps": [],
        "overall_status": "succeeded",
        "output_hashes": {},
    }

    steps: list[dict[str, object]] = []

    with TemporaryDirectory(prefix="archiv-acceptance-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        corpus_dir = tmp_dir / "corpus"
        archiv_home = tmp_dir / "archiv-home"
        backup_zip = tmp_dir / "backup.zip"
        restored_home = tmp_dir / "restored-home"
        # 1. Version check
        code, dur, out, err = run_cmd([sys.executable, "-m", "archiv.cli", "version"])
        steps.append(
            {
                "step": "version",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
                "version": out.strip(),
            }
        )
        report["archiv_version"] = out.strip()
        if code != 0:
            report["overall_status"] = "failed"

        # 2. Doctor check
        code, dur, out, err = run_cmd([sys.executable, "-m", "archiv.cli", "doctor", "--json"])
        steps.append(
            {
                "step": "doctor",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
            }
        )
        if code != 0:
            report["overall_status"] = "failed"

        # 3. Generate public safe fixtures
        gen_script = REPO_ROOT / "scripts" / "generate_fixture_corpus.py"
        code, dur, out, err = run_cmd(
            [sys.executable, str(gen_script), "--output", str(corpus_dir)]
        )
        steps.append(
            {
                "step": "generate_fixtures",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
            }
        )
        if code != 0:
            report["overall_status"] = "failed"

        # 4. Add public fixtures
        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "add",
                str(corpus_dir),
                "--home",
                str(archiv_home),
                "--json",
            ]
        )
        added_count = 0
        if code == 0 and out.strip():
            try:
                data = json.loads(out)
                added_count = len(data.get("added", []))
            except Exception:
                pass
        steps.append(
            {
                "step": "add_fixtures",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
                "count": added_count,
            }
        )
        if code != 0:
            report["overall_status"] = "failed"

        # 5. Find search check
        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "find",
                "unique fixture marker",
                "--home",
                str(archiv_home),
                "--json",
            ]
        )
        match_count = 0
        if code == 0 and out.strip():
            with contextlib.suppress(Exception):
                match_count = len(json.loads(out))
        steps.append(
            {
                "step": "find",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
                "match_count": match_count,
            }
        )
        if code != 0:
            report["overall_status"] = "failed"

        # 6. Ask test (disabled model failure closed check)
        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "ask",
                "What decisions were made?",
                "--home",
                str(archiv_home),
                "--json",
            ]
        )
        passed_ask = "disabled" in (out + err)
        steps.append(
            {
                "step": "ask_disabled_model",
                "status": "passed" if passed_ask else "failed",
                "duration_seconds": dur,
            }
        )
        if not passed_ask:
            report["overall_status"] = "failed"

        # 7. Deterministic Report Generation
        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "report",
                "unique fixture marker",
                "--deterministic",
                "--no-render",
                "--home",
                str(archiv_home),
                "--json",
            ]
        )
        report_error: str | None = None
        if code != 0:
            report_error = (out + err).strip()[:500]
        steps.append(
            {
                "step": "report_generation",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
                **({"error_output": report_error} if report_error else {}),
            }
        )
        if code != 0:
            report["overall_status"] = "failed"

        # 8. Status command check
        code, dur, out, err = run_cmd(
            [sys.executable, "-m", "archiv.cli", "status", "--home", str(archiv_home), "--json"]
        )
        steps.append(
            {
                "step": "status",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
            }
        )

        # 9. Backup & Restore
        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "backup",
                str(backup_zip),
                "--home",
                str(archiv_home),
                "--json",
            ]
        )
        backup_hash = sha256_file(backup_zip) if backup_zip.is_file() else ""
        steps.append(
            {
                "step": "backup",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
                "backup_sha256": backup_hash,
            }
        )

        code, dur, out, err = run_cmd(
            [
                sys.executable,
                "-m",
                "archiv.cli",
                "restore",
                str(backup_zip),
                "--home",
                str(restored_home),
                "--json",
            ]
        )
        steps.append(
            {
                "step": "restore",
                "status": "passed" if code == 0 else "failed",
                "duration_seconds": dur,
            }
        )

        # 10. Optional user folder processing
        if arguments.user_folder and arguments.user_folder.exists():
            user_home = tmp_dir / "user-home"
            code, dur, out, err = run_cmd(
                [
                    sys.executable,
                    "-m",
                    "archiv.cli",
                    "add",
                    str(arguments.user_folder.resolve()),
                    "--home",
                    str(user_home),
                    "--json",
                ]
            )
            steps.append(
                {
                    "step": "ingest_user_folder",
                    "status": "passed" if code == 0 else "failed",
                    "duration_seconds": dur,
                }
            )

        # Resource usage
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["max_rss_kb"] = usage.ru_maxrss
        report["user_time_seconds"] = round(usage.ru_utime, 4)
        report["system_time_seconds"] = round(usage.ru_stime, 4)
        report["steps"] = steps

        if any(s.get("status") == "failed" for s in steps):
            report["overall_status"] = "failed"

    formatted = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.write_text(formatted + "\n", encoding="utf-8")
        print(f"Acceptance report written to: {arguments.output}")
    else:
        print(formatted)

    return 0 if report["overall_status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
