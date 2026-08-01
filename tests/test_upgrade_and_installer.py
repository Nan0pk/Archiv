from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1].resolve()
INSTALLER = REPO_ROOT / "tools" / "install-fedora.sh"


def test_fedora_installer_local_source_and_upgrade(tmp_path: Path) -> None:
    prefix = tmp_path / "archiv-alpha"
    bin_dir = tmp_path / "bin"
    home = tmp_path / "archiv-home"

    env = dict(os.environ)
    env["ARCHIV_HOME"] = str(home)

    # 1. Simulate older version 0.1.0a2 installed
    old_version_dir = prefix / "versions" / "0.1.0a2"
    old_version_dir.mkdir(parents=True, exist_ok=True)
    (old_version_dir / "marker.txt").write_text("old-version-marker", encoding="utf-8")

    # Ingest sample vault into home before upgrade
    corpus = tmp_path / "corpus"
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

    # Verify archiv --version works without shell activation
    ver_res = subprocess.run([str(archiv_bin), "version"], capture_output=True, text=True, check=True)
    assert ver_res.stdout.strip() == "0.1.0a3"

    # Verify doctor works
    doc_res = subprocess.run([str(archiv_bin), "doctor", "--json"], capture_output=True, text=True, check=True)
    assert json.loads(doc_res.stdout)["status"] == "ok"

    # Verify install.json metadata
    install_json_path = prefix / "install.json"
    assert install_json_path.is_file()
    meta = json.loads(install_json_path.read_text(encoding="utf-8"))
    assert meta["version"] == "0.1.0a3"
    assert "source_commit" in meta

    # Verify older version directory is preserved and not unexpectedly deleted
    assert (old_version_dir / "marker.txt").is_file()
    assert (old_version_dir / "marker.txt").read_text(encoding="utf-8") == "old-version-marker"

    # Verify current symlink
    current_symlink = prefix / "current"
    assert current_symlink.resolve() == (prefix / "versions" / "0.1.0a3").resolve()
