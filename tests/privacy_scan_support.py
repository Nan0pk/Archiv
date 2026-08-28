"""Shared scanning logic for the git-tracked-file privacy guardrail.

Separated from test_privacy_and_artifacts.py so it can be exercised directly:
the guardrail that enforces the "no private paths" rule needs its own proof
that it actually enforces it, not just that the rule it currently encodes
happens to pass on this tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

BINARY_SUFFIXES = frozenset({".docx", ".pdf", ".png", ".wav", ".xlsx", ".pptx", ".zip"})


def tracked_files(repo_root: Path) -> list[Path]:
    """Every file git actually tracks -- never an untracked scratch or build file."""

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    names = result.stdout.decode("utf-8").split("\0")
    return [repo_root / name for name in names if name]


def files_containing(repo_root: Path, needle: str, *, exclude: Path | None = None) -> list[Path]:
    """Tracked, non-binary files whose text contains ``needle`` verbatim."""

    hits: list[Path] = []
    for path in tracked_files(repo_root):
        if path == exclude or path.suffix in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in content:
            hits.append(path)
    return hits
