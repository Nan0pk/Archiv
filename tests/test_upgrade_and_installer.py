from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from archiv import __version__ as ARCHIV_VERSION

REPO_ROOT = Path(__file__).parents[1].resolve()
INSTALLER = REPO_ROOT / "tools" / "install-fedora.sh"


def test_installer_refuses_python_older_than_the_supported_minimum(tmp_path: Path) -> None:
    """An unsupported interpreter must fail early with readable guidance.

    Without this check the installer builds a virtual environment and only
    fails deep inside pip's resolver, where the real cause is buried.
    """

    # Make the real interpreter report an unsupported version. The guard runs
    # before any other installer step, so nothing else is disturbed.
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import sys\n\nsys.version_info = (3, 11, 2, 'final', 0)\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(shim)

    result = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--source",
            str(REPO_ROOT),
            "--prefix",
            str(tmp_path / "archiv-alpha"),
            "--bin-dir",
            str(tmp_path / "target-bin"),
            "--skip-system-packages",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "requires Python 3.12 or newer" in result.stderr
    # It must fail before creating anything.
    assert not (tmp_path / "archiv-alpha" / "versions").exists()


def test_fedora_installer_local_source_and_upgrade(tmp_path: Path) -> None:
    prefix = tmp_path / "archiv-alpha"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "archiv-home"

    env = dict(os.environ)
    env["ARCHIV_HOME"] = str(home)

    # Simulate the prior 0.1.0a4 release already installed.
    old_version_dir = prefix / "versions" / "0.1.0a4"
    old_version_dir.mkdir(parents=True, exist_ok=True)
    (old_version_dir / "marker.txt").write_text("old-version-marker", encoding="utf-8")

    subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--source",
            str(REPO_ROOT),
            "--prefix",
            str(prefix),
            "--bin-dir",
            str(bin_dir),
            "--skip-system-packages",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )

    archiv_bin = bin_dir / "archiv"
    assert archiv_bin.is_file()

    ver_res = subprocess.run(
        [str(archiv_bin), "version"], capture_output=True, text=True, check=True
    )
    assert ver_res.stdout.strip() == ARCHIV_VERSION

    doc_res = subprocess.run(
        [str(archiv_bin), "doctor", "--json"], capture_output=True, text=True, check=True
    )
    assert json.loads(doc_res.stdout)["status"] == "ok"

    install_json_path = prefix / "install.json"
    assert install_json_path.is_file()
    meta = json.loads(install_json_path.read_text(encoding="utf-8"))
    assert meta["version"] == ARCHIV_VERSION
    assert "source_commit" in meta

    assert (old_version_dir / "marker.txt").is_file()
    assert (old_version_dir / "marker.txt").read_text(encoding="utf-8") == "old-version-marker"

    current_symlink = prefix / "current"
    assert current_symlink.resolve() == (prefix / "versions" / ARCHIV_VERSION).resolve()
