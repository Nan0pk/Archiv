"""Research-only, bounded inspection of candidate native InPage CFB containers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

CFB_MAGIC: Final = bytes.fromhex("D0CF11E0A1B11AE1")
FREESECT: Final = 0xFFFFFFFF
ENDOFCHAIN: Final = 0xFFFFFFFE
FATSECT: Final = 0xFFFFFFFD
DIFSECT: Final = 0xFFFFFFFC
MAXREGSECT: Final = 0xFFFFFFFA
INPAGE_STREAM_RE: Final = re.compile(r"^inpage\d{3}$", re.IGNORECASE)
SPLIT_SUFFIX_RE: Final = re.compile(r"^\.b\d{2}$", re.IGNORECASE)

ProbeClassification = Literal[
    "inpage_cfb_candidate",
    "split_inpage_cfb_candidate",
    "unrelated_cfb",
    "unrelated_inp",
    "not_ole",
    "malformed",
    "oversize",
]


@dataclass(frozen=True)
class ProbeLimits:
    """Hard limits for research inspection."""

    max_file_bytes: int = 64 * 1024 * 1024
    max_sector_size: int = 4096
    max_fat_sectors: int = 4096
    max_difat_sectors: int = 256
    max_directory_sectors: int = 4096
    max_directory_entries: int = 16_384
    max_streams: int = 4096
    max_directory_depth: int = 64


@dataclass(frozen=True)
class DirectoryEntry:
    """Minimal directory metadata; stream bytes are never opened."""

    entry_id: int
    name: str
    object_type: int
    left_sibling: int
    right_sibling: int
    child: int
    starting_sector: int
    stream_size: int


@dataclass(frozen=True)
class ProbeResult:
    """Stable machine-readable research result."""

    schema_version: int
    classification: ProbeClassification
    source_name: str
    source_suffix: str
    file_size: int
    file_sha256: str
    cfb_major_version: int | None
    sector_size: int | None
    directory_entry_count: int
    stream_count: int
    stream_names: tuple[str, ...]
    candidate_content_streams: tuple[str, ...]
    has_document_info: bool
    native_support_claimed: bool
    stream_contents_read: bool
    warnings: tuple[str, ...]
    error: str | None


class ProbeError(ValueError):
    """Expected bounded-parser failure."""


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _sector_offset(sector_id: int, sector_size: int) -> int:
    if sector_id > MAXREGSECT:
        raise ProbeError("reserved sector identifier used as data sector")
    return (sector_id + 1) * sector_size


def _read_sector(data: bytes, sector_id: int, sector_size: int) -> bytes:
    offset = _sector_offset(sector_id, sector_size)
    end = offset + sector_size
    if offset < sector_size or end > len(data):
        raise ProbeError("sector points outside the file")
    return data[offset:end]


def _collect_fat_sector_ids(
    data: bytes,
    *,
    sector_size: int,
    number_of_fat_sectors: int,
    first_difat_sector: int,
    number_of_difat_sectors: int,
    limits: ProbeLimits,
) -> list[int]:
    if number_of_fat_sectors > limits.max_fat_sectors:
        raise ProbeError("FAT sector limit exceeded")
    if number_of_difat_sectors > limits.max_difat_sectors:
        raise ProbeError("DIFAT sector limit exceeded")

    fat_sector_ids = [
        sector_id
        for sector_id in struct.unpack_from("<109I", data, 76)
        if sector_id != FREESECT
    ]
    if any(sector_id > MAXREGSECT for sector_id in fat_sector_ids):
        raise ProbeError("header DIFAT contains a reserved sector identifier")

    current = first_difat_sector
    seen: set[int] = set()
    entries_per_difat_sector = sector_size // 4 - 1
    for _ in range(number_of_difat_sectors):
        if current in {FREESECT, ENDOFCHAIN}:
            raise ProbeError("DIFAT chain ended before its declared length")
        if current in seen:
            raise ProbeError("DIFAT chain contains a cycle")
        seen.add(current)
        sector = _read_sector(data, current, sector_size)
        values = struct.unpack_from(f"<{entries_per_difat_sector + 1}I", sector)
        for sector_id in values[:-1]:
            if sector_id == FREESECT:
                continue
            if sector_id > MAXREGSECT:
                raise ProbeError("DIFAT contains a reserved FAT sector identifier")
            fat_sector_ids.append(sector_id)
        current = values[-1]

    if number_of_difat_sectors == 0 and first_difat_sector not in {FREESECT, ENDOFCHAIN}:
        raise ProbeError("unexpected DIFAT start sector")
    if number_of_difat_sectors and current not in {FREESECT, ENDOFCHAIN}:
        raise ProbeError("DIFAT chain exceeds its declared length")
    if len(fat_sector_ids) < number_of_fat_sectors:
        raise ProbeError("fewer FAT sectors found than declared")
    return fat_sector_ids[:number_of_fat_sectors]


def _read_fat(data: bytes, fat_sector_ids: list[int], sector_size: int) -> list[int]:
    entries_per_sector = sector_size // 4
    fat: list[int] = []
    for sector_id in fat_sector_ids:
        sector = _read_sector(data, sector_id, sector_size)
        fat.extend(struct.unpack_from(f"<{entries_per_sector}I", sector))
    return fat


def _follow_chain(
    first_sector: int,
    fat: list[int],
    *,
    maximum_sectors: int,
    label: str,
) -> list[int]:
    if first_sector in {FREESECT, ENDOFCHAIN}:
        return []
    current = first_sector
    seen: set[int] = set()
    result: list[int] = []
    while current != ENDOFCHAIN:
        if current in {FREESECT, FATSECT, DIFSECT} or current > MAXREGSECT:
            raise ProbeError(f"{label} chain contains a reserved sector identifier")
        if current >= len(fat):
            raise ProbeError(f"{label} chain points outside the FAT")
        if current in seen:
            raise ProbeError(f"{label} chain contains a cycle")
        if len(result) >= maximum_sectors:
            raise ProbeError(f"{label} sector limit exceeded")
        seen.add(current)
        result.append(current)
        current = fat[current]
    return result


def _decode_directory_entry(raw: bytes, *, entry_id: int) -> DirectoryEntry | None:
    if len(raw) != 128:
        raise ProbeError("truncated directory entry")
    object_type = raw[66]
    if object_type == 0:
        return None
    if object_type not in {1, 2, 5}:
        raise ProbeError("directory entry has an unsupported object type")

    name_length = _u16(raw, 64)
    if name_length < 2 or name_length > 64 or name_length % 2:
        raise ProbeError("directory entry has an invalid name length")
    name_bytes = raw[: name_length - 2]
    try:
        name = name_bytes.decode("utf-16le", errors="strict")
    except UnicodeDecodeError as error:
        raise ProbeError("directory entry name is not valid UTF-16LE") from error
    if not name or "\x00" in name:
        raise ProbeError("directory entry has an invalid name")
    return DirectoryEntry(
        entry_id=entry_id,
        name=name,
        object_type=object_type,
        left_sibling=_u32(raw, 68),
        right_sibling=_u32(raw, 72),
        child=_u32(raw, 76),
        starting_sector=_u32(raw, 116),
        stream_size=_u64(raw, 120),
    )


def _parse_directory(
    data: bytes,
    *,
    sector_size: int,
    first_directory_sector: int,
    fat: list[int],
    limits: ProbeLimits,
) -> list[DirectoryEntry | None]:
    directory_sector_ids = _follow_chain(
        first_directory_sector,
        fat,
        maximum_sectors=limits.max_directory_sectors,
        label="directory",
    )
    entries: list[DirectoryEntry | None] = []
    for sector_id in directory_sector_ids:
        sector = _read_sector(data, sector_id, sector_size)
        for offset in range(0, sector_size, 128):
            entry_id = len(entries)
            entry = _decode_directory_entry(
                sector[offset : offset + 128],
                entry_id=entry_id,
            )
            entries.append(entry)
            if len(entries) > limits.max_directory_entries:
                raise ProbeError("directory entry limit exceeded")
    return entries


def _reachable_stream_names(
    entries: list[DirectoryEntry | None],
    *,
    limits: ProbeLimits,
) -> tuple[tuple[str, ...], int, int]:
    if not entries or entries[0] is None or entries[0].object_type != 5:
        raise ProbeError("CFB directory has no root entry")
    root = entries[0]
    if root.name.casefold() != "root entry":
        raise ProbeError("CFB root directory entry has an unexpected name")

    stream_names: list[str] = []
    seen: set[int] = {0}
    stack: list[tuple[int, tuple[str, ...], int]] = []
    if root.child != FREESECT:
        stack.append((root.child, (), 1))

    while stack:
        entry_id, parent_path, depth = stack.pop()
        if depth > limits.max_directory_depth:
            raise ProbeError("directory tree depth limit exceeded")
        if entry_id == FREESECT:
            continue
        if entry_id >= len(entries):
            raise ProbeError("directory tree points outside the directory")
        if entry_id in seen:
            raise ProbeError("directory tree contains a cycle or duplicate reference")
        entry = entries[entry_id]
        if entry is None:
            raise ProbeError("directory tree references an empty entry")
        if entry.object_type == 5:
            raise ProbeError("directory tree contains a second root entry")
        seen.add(entry_id)

        if entry.left_sibling != FREESECT:
            stack.append((entry.left_sibling, parent_path, depth + 1))
        if entry.right_sibling != FREESECT:
            stack.append((entry.right_sibling, parent_path, depth + 1))

        if entry.object_type == 1:
            if entry.child != FREESECT:
                stack.append((entry.child, parent_path + (entry.name,), depth + 1))
        elif entry.object_type == 2:
            stream_names.append("/".join(parent_path + (entry.name,)))
            if len(stream_names) > limits.max_streams:
                raise ProbeError("stream count limit exceeded")

    nonempty_count = sum(entry is not None for entry in entries)
    orphan_count = nonempty_count - len(seen)
    return tuple(sorted(stream_names, key=str.casefold)), len(seen), orphan_count


def _classify(
    *,
    source_suffix: str,
    stream_names: tuple[str, ...],
) -> tuple[ProbeClassification, tuple[str, ...], bool]:
    root_streams = tuple(name for name in stream_names if "/" not in name)
    folded = {name.casefold(): name for name in root_streams}
    has_document_info = "documentinfo" in folded
    candidate_streams = tuple(
        sorted(
            (
                original
                for folded_name, original in folded.items()
                if INPAGE_STREAM_RE.fullmatch(folded_name)
            ),
            key=str.casefold,
        )
    )
    if has_document_info and candidate_streams:
        if SPLIT_SUFFIX_RE.fullmatch(source_suffix):
            return "split_inpage_cfb_candidate", candidate_streams, True
        return "inpage_cfb_candidate", candidate_streams, True
    return "unrelated_cfb", candidate_streams, has_document_info


def probe_path(path: Path, *, limits: ProbeLimits = ProbeLimits()) -> ProbeResult:
    """Inspect CFB metadata without reading any stream content."""

    source_name = path.name
    source_suffix = path.suffix.lower()
    if not path.exists():
        return ProbeResult(
            schema_version=1,
            classification="malformed",
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=0,
            file_sha256="",
            cfb_major_version=None,
            sector_size=None,
            directory_entry_count=0,
            stream_count=0,
            stream_names=(),
            candidate_content_streams=(),
            has_document_info=False,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=(),
            error="input file does not exist",
        )
    if not path.is_file():
        return ProbeResult(
            schema_version=1,
            classification="malformed",
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=0,
            file_sha256="",
            cfb_major_version=None,
            sector_size=None,
            directory_entry_count=0,
            stream_count=0,
            stream_names=(),
            candidate_content_streams=(),
            has_document_info=False,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=(),
            error="input path is not a regular file",
        )

    file_size = path.stat().st_size
    if file_size > limits.max_file_bytes:
        return ProbeResult(
            schema_version=1,
            classification="oversize",
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=file_size,
            file_sha256="",
            cfb_major_version=None,
            sector_size=None,
            directory_entry_count=0,
            stream_count=0,
            stream_names=(),
            candidate_content_streams=(),
            has_document_info=False,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=(),
            error="file size limit exceeded before parsing",
        )

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not data.startswith(CFB_MAGIC):
        classification: ProbeClassification = (
            "unrelated_inp" if source_suffix == ".inp" else "not_ole"
        )
        return ProbeResult(
            schema_version=1,
            classification=classification,
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=file_size,
            file_sha256=digest,
            cfb_major_version=None,
            sector_size=None,
            directory_entry_count=0,
            stream_count=0,
            stream_names=(),
            candidate_content_streams=(),
            has_document_info=False,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=(),
            error=None,
        )

    major_version: int | None = None
    sector_size: int | None = None
    try:
        if len(data) < 512:
            raise ProbeError("truncated CFB header")
        major_version = _u16(data, 26)
        byte_order = _u16(data, 28)
        sector_shift = _u16(data, 30)
        mini_sector_shift = _u16(data, 32)
        if byte_order != 0xFFFE:
            raise ProbeError("unsupported CFB byte order")
        if major_version not in {3, 4}:
            raise ProbeError("unsupported CFB major version")
        expected_shift = 9 if major_version == 3 else 12
        if sector_shift != expected_shift:
            raise ProbeError("CFB sector size does not match its major version")
        if mini_sector_shift != 6:
            raise ProbeError("unsupported CFB mini-sector size")
        sector_size = 1 << sector_shift
        if sector_size > limits.max_sector_size:
            raise ProbeError("sector size limit exceeded")
        if len(data) < sector_size or len(data) % sector_size:
            raise ProbeError("CFB file is not aligned to its sector size")
        if major_version == 3 and _u32(data, 40) != 0:
            raise ProbeError("version 3 CFB declares directory sector count")
        number_of_fat_sectors = _u32(data, 44)
        first_directory_sector = _u32(data, 48)
        first_difat_sector = _u32(data, 68)
        number_of_difat_sectors = _u32(data, 72)

        fat_sector_ids = _collect_fat_sector_ids(
            data,
            sector_size=sector_size,
            number_of_fat_sectors=number_of_fat_sectors,
            first_difat_sector=first_difat_sector,
            number_of_difat_sectors=number_of_difat_sectors,
            limits=limits,
        )
        fat = _read_fat(data, fat_sector_ids, sector_size)
        entries = _parse_directory(
            data,
            sector_size=sector_size,
            first_directory_sector=first_directory_sector,
            fat=fat,
            limits=limits,
        )
        stream_names, reachable_entry_count, orphan_entry_count = _reachable_stream_names(
            entries,
            limits=limits,
        )
        classification, candidate_streams, has_document_info = _classify(
            source_suffix=source_suffix,
            stream_names=stream_names,
        )
        warnings: list[str] = [
            "Research candidate only; stream names do not establish native text or layout support."
        ]
        if candidate_streams and not has_document_info:
            warnings.append("InPage-like content stream exists without DocumentInfo.")
        if candidate_streams and all(
            name.casefold() != "inpage100" for name in candidate_streams
        ):
            warnings.append(
                "Stream generation is a discovery lead, not independently verified format evidence."
            )
        if orphan_entry_count:
            warnings.append(
                f"Ignored {orphan_entry_count} non-empty orphan directory entries."
            )
        return ProbeResult(
            schema_version=1,
            classification=classification,
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=file_size,
            file_sha256=digest,
            cfb_major_version=major_version,
            sector_size=sector_size,
            directory_entry_count=reachable_entry_count,
            stream_count=len(stream_names),
            stream_names=stream_names,
            candidate_content_streams=candidate_streams,
            has_document_info=has_document_info,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=tuple(warnings),
            error=None,
        )
    except (OSError, ProbeError, struct.error) as error:
        return ProbeResult(
            schema_version=1,
            classification="malformed",
            source_name=source_name,
            source_suffix=source_suffix,
            file_size=file_size,
            file_sha256=digest,
            cfb_major_version=major_version,
            sector_size=sector_size,
            directory_entry_count=0,
            stream_count=0,
            stream_names=(),
            candidate_content_streams=(),
            has_document_info=False,
            native_support_claimed=False,
            stream_contents_read=False,
            warnings=(),
            error=str(error),
        )


def result_json(result: ProbeResult) -> str:
    """Serialize a result deterministically."""

    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    """Run the bounded research probe."""

    parser = argparse.ArgumentParser(
        description="Inspect candidate InPage CFB metadata without claiming native support."
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    result = probe_path(cast(Path, args.path))
    print(result_json(result))
    return 0 if result.classification not in {"malformed", "oversize"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
