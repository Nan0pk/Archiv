"""Safe archive metadata and member listing normalization.

Inspects ZIP and TAR container metadata without unpacking hostile contents.
Encrypted archives are marked locked without attempting passwords.
"""

from __future__ import annotations

import tarfile
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile, is_zipfile

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.ingestion.formats import MalformedInputError
from archiv.ingestion.limits import MAX_INPUT_BYTES, check_tar, check_zip


def normalize_archive(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    kind: str = "archive",
) -> NormalizedDocument:
    """Inspect archive contents, record member listings, and detect encryption."""

    if path.stat().st_size > MAX_INPUT_BYTES:
        raise MalformedInputError(f"archive exceeds maximum input limit of {MAX_INPUT_BYTES} bytes")

    segments: list[NormalizedSegment] = []
    member_details: list[dict[str, object]] = []
    is_locked = False
    lock_reason: str | None = None
    total_uncompressed = 0
    archive_format = "unknown"

    if is_zipfile(path):
        archive_format = "zip"
        check_zip(path)
        try:
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    if bool(member.flag_bits & 0x1):
                        is_locked = True
                        lock_reason = "encrypted"
                    total_uncompressed += member.file_size
                    date_iso = (
                        f"{member.date_time[0]:04d}-{member.date_time[1]:02d}-{member.date_time[2]:02d}"
                        f"T{member.date_time[3]:02d}:{member.date_time[4]:02d}:{member.date_time[5]:02d}Z"
                    )
                    compression = "deflate" if member.compress_type == 8 else "stored"
                    member_details.append(
                        {
                            "internal_path": member.filename,
                            "size": member.file_size,
                            "modified_at": date_iso,
                            "compression": compression,
                        }
                    )
                    segments.append(
                        NormalizedSegment(
                            locator={"internal_path": member.filename},
                            text=member.filename,
                        )
                    )
        except BadZipFile as error:
            raise MalformedInputError(f"malformed ZIP container: {error}") from error
    elif tarfile.is_tarfile(path):
        archive_format = "tar"
        check_tar(path)
        try:
            with tarfile.open(path, "r:*") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    total_uncompressed += member.size
                    date_iso = datetime.fromtimestamp(member.mtime, UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    member_details.append(
                        {
                            "internal_path": member.name,
                            "size": member.size,
                            "modified_at": date_iso,
                            "compression": "tar",
                        }
                    )
                    segments.append(
                        NormalizedSegment(
                            locator={"internal_path": member.name},
                            text=member.name,
                        )
                    )
        except (tarfile.TarError, OSError, EOFError) as error:
            raise MalformedInputError(f"malformed TAR container: {error}") from error
    else:
        raise MalformedInputError("unrecognized or malformed archive format")

    metadata: dict[str, object] = {
        "format": archive_format,
        "archive_locked": is_locked,
        "reason": lock_reason,
        "member_count": len(member_details),
        "total_uncompressed_bytes": total_uncompressed,
        "members": member_details,
    }

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="archive",
        source_name=source_name,
        segments=segments,
        metadata=metadata,
    )
