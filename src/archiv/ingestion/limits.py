"""Shared fail-closed resource limits for hostile ingestion inputs."""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, is_zipfile

MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4_096
MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 200
# Rendering every page is the expensive, attackable operation, so OCR keeps the tight
# bound (visual_ocr.MAX_PDF_PAGES). Native text extraction renders nothing: its cost is
# linear in a file already capped at MAX_INPUT_BYTES, so it only needs a runaway guard.
MAX_PAGES = 250
MAX_NATIVE_PAGES = 10_000
MAX_IMAGE_PIXELS = 80_000_000
MAX_RECURSION_DEPTH = 8
MAX_SUBPROCESSES = 1
MAX_CPU_SECONDS = 60
MAX_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 60


class LimitExceededError(ValueError):
    """A named ingestion safety boundary was exceeded."""


class NativeResourceLimitError(LimitExceededError):
    """A resource bound was reached on an otherwise well-formed input.

    Distinct from LimitExceededError's other raisers (hostile archive structure,
    oversized inputs) so callers can tell "this document is too big" apart from
    "this document is dangerous" without inspecting the error message.
    """


def _fail(name: str, actual: int, maximum: int) -> None:
    raise LimitExceededError(f"{name} limit exceeded: {actual} > {maximum}")


def check_input(path: Path) -> None:
    """Reject oversized, non-regular, linked, or dangerous container inputs."""

    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise LimitExceededError("symbolic-link inputs are not allowed")
    if not stat.S_ISREG(info.st_mode):
        raise LimitExceededError("input must be a regular file")
    if info.st_size > MAX_INPUT_BYTES:
        _fail("input bytes", info.st_size, MAX_INPUT_BYTES)
    if is_zipfile(path):
        check_zip(path)


def check_zip(path: Path) -> None:
    """Inspect ZIP metadata without extracting members to the filesystem."""

    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                _fail("archive entries", len(members), MAX_ARCHIVE_ENTRIES)
            total = 0
            names: set[str] = set()
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                if not name or name.startswith(("/", "\\")) or "\\" in name or ".." in parts:
                    raise LimitExceededError(f"unsafe member path: {name!r}")
                if name in names:
                    raise LimitExceededError(f"duplicate archive member: {name!r}")
                names.add(name)
                depth = len([part for part in parts if part not in {"", "."}])
                if depth > MAX_RECURSION_DEPTH:
                    _fail("archive path recursion", depth, MAX_RECURSION_DEPTH)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise LimitExceededError(f"archive symbolic link is not allowed: {name!r}")
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    _fail("archive member bytes", member.file_size, MAX_ARCHIVE_MEMBER_BYTES)
                total += member.file_size
                if total > MAX_EXPANDED_BYTES:
                    _fail("archive expanded bytes", total, MAX_EXPANDED_BYTES)
                if member.file_size and not member.compress_size:
                    raise LimitExceededError("archive member has invalid zero compressed size")
                if (
                    member.compress_size
                    and member.file_size / member.compress_size > MAX_EXPANSION_RATIO
                ):
                    _fail(
                        "archive expansion ratio",
                        member.file_size // member.compress_size,
                        MAX_EXPANSION_RATIO,
                    )
    except BadZipFile as error:
        raise LimitExceededError("malformed ZIP container") from error


def check_pages(count: int) -> None:
    if count > MAX_PAGES:
        _fail("pages", count, MAX_PAGES)


def check_native_pages(count: int) -> None:
    """Bound page-structure work that extracts text without rendering anything."""

    if count > MAX_NATIVE_PAGES:
        raise NativeResourceLimitError(f"native pages limit exceeded: {count} > {MAX_NATIVE_PAGES}")


def check_image(width: int, height: int) -> None:
    pixels = width * height
    if width <= 0 or height <= 0 or pixels > MAX_IMAGE_PIXELS:
        _fail("image pixels", pixels, MAX_IMAGE_PIXELS)
