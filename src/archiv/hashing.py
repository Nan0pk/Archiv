"""Small deterministic hashing helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from archiv.contracts import FileEvidence


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(data).hexdigest()


def file_evidence(path: Path, *, display_path: str | None = None) -> FileEvidence:
    """Read a file and return stable digest evidence."""

    data = path.read_bytes()
    return FileEvidence(
        path=display_path or path.as_posix(),
        sha256=sha256_bytes(data),
        size=len(data),
    )
