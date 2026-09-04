"""Small deterministic hashing helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from archiv.contracts import FileEvidence


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""

    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file into a lowercase SHA-256 digest."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def copy_and_hash(source: Path, target: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream copy source to target while computing its lowercase SHA-256 digest."""

    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("wb") as dst:
        while chunk := src.read(chunk_size):
            digest.update(chunk)
            dst.write(chunk)
    return digest.hexdigest()


def file_evidence(path: Path, *, display_path: str | None = None) -> FileEvidence:
    """Read a file and return stable digest evidence."""

    data = path.read_bytes()
    return FileEvidence(
        path=display_path or path.as_posix(),
        sha256=sha256_bytes(data),
        size=len(data),
    )
