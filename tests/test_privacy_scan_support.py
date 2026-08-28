from __future__ import annotations

import subprocess
from pathlib import Path

from privacy_scan_support import files_containing


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_untracked_files_are_never_scanned(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "tracked.md").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "scratch.md").write_text("/home/secret-user leaked here", encoding="utf-8")

    hits = files_containing(tmp_path, "/home/secret-user")

    assert hits == []


def test_tracked_files_containing_the_needle_are_found(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "docs.md").write_text("see /home/secret-user for details", encoding="utf-8")
    subprocess.run(["git", "add", "docs.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    hits = files_containing(tmp_path, "/home/secret-user")

    assert hits == [tmp_path / "docs.md"]


def test_a_word_that_merely_resembles_the_account_name_is_not_a_match(tmp_path: Path) -> None:
    """A path-shaped needle must not fire on a word that happens to share letters
    with the account name -- e.g. hardware named the same as a contributor's account.
    Matching the bare username token (the old design) could not tell these apart;
    matching the full home-directory path can. Uses this process's own account name
    as the "coincidental word", so the case that motivated this fix (an account name
    that collides with hardware this project documents on purpose) is exercised
    directly rather than with an arbitrary fictional name -- without embedding this
    machine's real home path as a literal string in the tree, which would trip the
    very guardrail this test supports.
    """

    account_name = Path.home().name
    _init_repo(tmp_path)
    (tmp_path / "hardware.md").write_text(
        f"Tested on an HP {account_name.title()} laptop.", encoding="utf-8"
    )
    subprocess.run(["git", "add", "hardware.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    hits = files_containing(tmp_path, str(Path.home()))

    assert hits == []
