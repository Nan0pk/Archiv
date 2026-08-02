"""Bounded CFB stream selection and InPage300 research extraction."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import re
import struct
import unicodedata
from pathlib import Path
from typing import Final

from archiv.research.inpage_cfb_probe import (
    FREESECT,
    DirectoryEntry,
    ProbeLimits,
    _collect_fat_sector_ids,
    _follow_chain,
    _parse_directory,
    _read_fat,
    _read_sector,
    _u16,
    _u32,
    probe_path,
)
from archiv.research.inpage_types import (
    BIDI_CONTROLS,
    JOINING_CONTROLS,
    ExtractionError,
    ExtractionLimits,
    RootStream,
    TextMetrics,
    basic_text_counts,
    is_arabic,
    sha256,
    text_sha256,
)

INPAGE_RE: Final = re.compile(r"^InPage(?P<variant>\d{3})$", re.IGNORECASE)


def _walk_root_entries(
    entries: list[DirectoryEntry | None], root: DirectoryEntry, limits: ProbeLimits
) -> dict[str, DirectoryEntry]:
    stack = [] if root.child == FREESECT else [root.child]
    seen: set[int] = set()
    streams: dict[str, DirectoryEntry] = {}
    while stack:
        entry_id = stack.pop()
        if entry_id == FREESECT:
            continue
        if entry_id >= len(entries) or entry_id in seen:
            raise ExtractionError("invalid or cyclic root directory tree")
        if len(seen) >= limits.max_directory_entries:
            raise ExtractionError("root directory entry limit exceeded")
        seen.add(entry_id)
        entry = entries[entry_id]
        if entry is None:
            raise ExtractionError("root directory points to an empty entry")
        stack.extend((entry.left_sibling, entry.right_sibling))
        if entry.object_type == 2:
            folded = entry.name.casefold()
            if folded in streams:
                raise ExtractionError("duplicate root stream name")
            streams[folded] = entry
    return streams


def read_native_root_stream(path: Path, *, limits: ExtractionLimits | None = None) -> RootStream:
    """Read exactly one validated root-level ``InPageNNN`` regular stream."""

    limits = limits or ExtractionLimits()
    probe = probe_path(path, limits=limits.cfb)
    if probe.classification not in {
        "inpage_cfb_candidate",
        "split_inpage_cfb_candidate",
    }:
        raise ExtractionError(probe.error or f"not an InPage CFB candidate: {probe.classification}")
    data = path.read_bytes()
    sector_size = 1 << _u16(data, 30)
    fat_sector_ids = _collect_fat_sector_ids(
        data,
        sector_size=sector_size,
        number_of_fat_sectors=_u32(data, 44),
        first_difat_sector=_u32(data, 68),
        number_of_difat_sectors=_u32(data, 72),
        limits=limits.cfb,
    )
    fat = _read_fat(data, fat_sector_ids, sector_size)
    entries = _parse_directory(
        data,
        sector_size=sector_size,
        first_directory_sector=_u32(data, 48),
        fat=fat,
        limits=limits.cfb,
    )
    if not entries or entries[0] is None or entries[0].object_type != 5:
        raise ExtractionError("missing CFB root entry")
    streams = _walk_root_entries(entries, entries[0], limits.cfb)
    if "documentinfo" not in streams:
        raise ExtractionError("missing root-level DocumentInfo stream")
    candidates = [entry for entry in streams.values() if INPAGE_RE.fullmatch(entry.name)]
    if len(candidates) != 1:
        raise ExtractionError(f"expected one root-level InPageNNN stream, found {len(candidates)}")
    entry = candidates[0]
    match = INPAGE_RE.fullmatch(entry.name)
    if match is None:
        raise ExtractionError("candidate stream name changed during validation")
    if entry.stream_size < 4096:
        raise ExtractionError("mini-stream native content is outside this research boundary")
    if entry.stream_size > limits.max_stream_bytes:
        raise ExtractionError("native stream size limit exceeded")
    expected_sectors = math.ceil(entry.stream_size / sector_size)
    sector_ids = _follow_chain(
        entry.starting_sector,
        fat,
        maximum_sectors=expected_sectors + 1,
        label="native InPage stream",
    )
    if len(sector_ids) != expected_sectors:
        raise ExtractionError("native stream sector count does not match declared size")
    payload = b"".join(_read_sector(data, sector_id, sector_size) for sector_id in sector_ids)
    payload = payload[: entry.stream_size]
    return RootStream(
        name=entry.name,
        variant=match.group("variant"),
        stream_size=entry.stream_size,
        stream_sha256=sha256(payload),
        sector_size=sector_size,
        sector_count=len(sector_ids),
        payload=payload,
    )


def extract_inpage300(
    stream: RootStream, *, limits: ExtractionLimits | None = None
) -> tuple[TextMetrics, str]:
    """Conservatively extract aligned UTF-16LE runs without publishing text."""

    limits = limits or ExtractionLimits()
    if stream.variant != "300":
        raise ExtractionError(f"expected InPage300, got {stream.name}")
    usable = stream.payload[: len(stream.payload) - len(stream.payload) % 2]
    units = struct.unpack(f"<{len(usable) // 2}H", usable)
    runs: list[list[int]] = []
    current: list[int] = []
    rejected_regions = 0
    rejected_bytes = 0
    in_rejected_region = False
    longest = 0
    unpaired_surrogates = 0

    def flush() -> None:
        nonlocal current
        if len(current) >= limits.min_utf16_run_units and any(is_arabic(unit) for unit in current):
            runs.append(current)
        current = []

    for unit in units:
        allowed = (
            unit in {0x0009, 0x000A, 0x000D, 0x00A0}
            or 0x0020 <= unit <= 0x007E
            or is_arabic(unit)
            or unit in BIDI_CONTROLS
            or unit in JOINING_CONTROLS
        )
        if 0xD800 <= unit <= 0xDFFF:
            unpaired_surrogates += 1
            allowed = False
        if allowed:
            current.append(unit)
            longest = max(longest, len(current))
            in_rejected_region = False
        else:
            if current:
                flush()
            if not in_rejected_region:
                rejected_regions += 1
                in_rejected_region = True
            rejected_bytes += 2
    flush()
    text = "\n".join(struct.pack(f"<{len(run)}H", *run).decode("utf-16le") for run in runs)
    if len(text) > limits.max_text_codepoints:
        raise ExtractionError("extracted text codepoint limit exceeded")
    nfc = unicodedata.normalize("NFC", text)
    count, arabic, ascii_count, replacement = basic_text_counts(nfc)
    metrics = TextMetrics(
        schema_version=1,
        variant="300",
        stream_sha256=stream.stream_sha256,
        stream_size=stream.stream_size,
        raw_text_sha256=text_sha256(text),
        nfc_text_sha256=text_sha256(nfc),
        text_codepoints=count,
        arabic_codepoints=arabic,
        ascii_codepoints=ascii_count,
        replacement_characters=replacement,
        line_count=0 if not nfc else nfc.count("\n") + 1,
        native_support_claimed=False,
        text_emitted=False,
        details={
            "utf16_code_units": len(units),
            "arabic_code_units": sum(is_arabic(unit) for unit in units),
            "ascii_printable_code_units": sum(0x20 <= unit <= 0x7E for unit in units),
            "zero_code_units": units.count(0),
            "unpaired_surrogates": unpaired_surrogates,
            "longest_allowed_run_code_units": longest,
            "accepted_run_count": len(runs),
            "rejected_region_count": rejected_regions,
            "rejected_bytes": rejected_bytes,
        },
    )
    return metrics, nfc
