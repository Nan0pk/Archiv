"""Bounded InPage100 framing, mapping and text research."""

from __future__ import annotations

import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Final

from archiv.research.inpage_types import (
    ExtractionError,
    ExtractionLimits,
    MappingComparison,
    MappingTable,
    RootStream,
    TextMetrics,
    basic_text_counts,
    is_arabic,
    sha256,
    text_sha256,
)

SPECIAL_ESCAPES: Final[Mapping[int, str]] = {
    0x09: "\t",
    0x0A: "\n",
    0x0B: "\n",
    0x0C: "\n",
    0x0D: "\n",
    0x20: " ",
    0x3A: "^",
    0xCB: "ﷲ",
    0xDA: "!",
    0xDB: "}",
    0xDC: "{",
    0xDD: "$",
    0xDF: "/",
    0xE0: "…",
    0xE1: ")",
    0xE2: "(",
    0xE3: "*",
    0xE4: "+",
    0xE9: ":",
    0xEB: "×",
    0xEC: "=",
    0xEF: "÷",
    0xF5: "−",
    0xF6: "ﷺ",
    0xFA: "]",
    0xFB: "[",
    0xFC: ".",
    0xFD: "‘",
    0xFE: "’",
}


def _scan_records(
    payload: bytes, *, use_u16_length: bool, limits: ExtractionLimits
) -> tuple[tuple[tuple[int, int], ...], int]:
    records: list[tuple[int, int]] = []
    nonzero_upper_words = 0
    position = 0
    while position + 4 <= len(payload):
        upper_word = 0
        if use_u16_length:
            length = struct.unpack_from("<H", payload, position)[0]
            upper_word = struct.unpack_from("<H", payload, position + 2)[0]
        else:
            length = struct.unpack_from("<I", payload, position)[0]
        end = position + 4 + length
        valid = (
            1 <= length <= limits.max_record_bytes
            and end <= len(payload)
            and payload[end - 1] == 0x0D
        )
        if valid:
            records.append((position, length))
            if use_u16_length and upper_word != 0:
                nonzero_upper_words += 1
            if len(records) > limits.max_records:
                raise ExtractionError("plausible record limit exceeded")
            position = end
        else:
            position += 1
    return tuple(records), nonzero_upper_words


def _decode_record(
    record: bytes, mapping: Mapping[int, str] | None
) -> tuple[str, int, int]:
    output: list[str] = []
    escapes = 0
    unmapped = 0
    index = 0
    while index < len(record) - 1:
        byte = record[index]
        if byte == 0x04 and index + 1 < len(record) - 1:
            index += 1
            code = record[index]
            if 0x09 <= code <= 0xFE:
                escapes += 1
                if code in SPECIAL_ESCAPES:
                    output.append(SPECIAL_ESCAPES[code])
                elif mapping is not None and code in mapping:
                    output.append(mapping[code])
                else:
                    output.append("\uFFFD")
                    unmapped += 1
        elif byte in range(0x09, 0x0E) or 0x20 <= byte <= 0xFE:
            output.append("\n" if byte in {0x0A, 0x0D} else chr(byte))
        index += 1
    return "".join(output), escapes, unmapped


def extract_inpage100(
    stream: RootStream,
    *,
    mapping: MappingTable | None = None,
    limits: ExtractionLimits | None = None,
) -> tuple[TextMetrics, str]:
    """Measure both framing interpretations and decode labelled u32 candidates."""

    limits = limits or ExtractionLimits()
    if stream.variant != "100":
        raise ExtractionError(f"expected InPage100, got {stream.name}")
    u32_records, _ = _scan_records(
        stream.payload, use_u16_length=False, limits=limits
    )
    u16_records, nonzero_upper = _scan_records(
        stream.payload, use_u16_length=True, limits=limits
    )
    u32_offsets = {offset for offset, _ in u32_records}
    u16_offsets = {offset for offset, _ in u16_records}
    lines: list[str] = []
    record_bytes = record_escapes = unmapped = dropped = blank = 0
    values = mapping.values if mapping is not None else None
    for offset, length in u32_records:
        record = stream.payload[offset + 4 : offset + 4 + length]
        record_bytes += len(record)
        decoded, escapes, missing = _decode_record(record, values)
        record_escapes += escapes
        unmapped += missing
        for line in decoded.split("\n"):
            stripped = line.rstrip()
            if not stripped.strip():
                blank += 1
                continue
            arabic = sum(is_arabic(ord(character)) for character in stripped)
            foreign = sum(
                0x80 <= ord(character) <= 0x05FF for character in stripped
            )
            latin_text = re.search(r"[A-Za-z]{2}", stripped) is not None
            latin_shape = (
                len(stripped) >= 5
                or " " in stripped
                or any(character.isdigit() for character in stripped)
            )
            if arabic or (not foreign and latin_text and latin_shape):
                lines.append(unicodedata.normalize("NFC", stripped))
            else:
                dropped += 1
    text = "\n".join(lines)
    if len(text) > limits.max_text_codepoints:
        raise ExtractionError("extracted text codepoint limit exceeded")
    count, arabic, ascii_count, replacement = basic_text_counts(text)
    all_escapes = sum(
        stream.payload[index] == 0x04
        and 0x09 <= stream.payload[index + 1] <= 0xFE
        for index in range(len(stream.payload) - 1)
    )
    return TextMetrics(
        schema_version=1,
        variant="100",
        stream_sha256=stream.stream_sha256,
        stream_size=stream.stream_size,
        raw_text_sha256=text_sha256(text),
        nfc_text_sha256=text_sha256(text),
        text_codepoints=count,
        arabic_codepoints=arabic,
        ascii_codepoints=ascii_count,
        replacement_characters=replacement,
        line_count=len(lines),
        native_support_claimed=False,
        text_emitted=False,
        details={
            "framing_assumption": "u32_length_with_parallel_u16_measurement",
            "all_04_escape_pairs": all_escapes,
            "plausible_u32_length_records": len(u32_records),
            "plausible_u16_length_records": len(u16_records),
            "shared_record_offsets": len(u32_offsets & u16_offsets),
            "u32_only_record_offsets": len(u32_offsets - u16_offsets),
            "u16_only_record_offsets": len(u16_offsets - u32_offsets),
            "nonzero_upper_length_words": nonzero_upper,
            "plausible_record_payload_bytes": record_bytes,
            "04_escape_pairs_inside_plausible_records": record_escapes,
            "unmapped_escape_pairs": unmapped,
            "dropped_lines": dropped,
            "blank_lines": blank,
            "mapping_sha256": mapping.source_sha256 if mapping is not None else "",
        },
    ), text


def parse_mapping_xml(
    data: bytes, *, limits: ExtractionLimits | None = None
) -> MappingTable:
    """Parse bounded ``InpageToUni.xml`` data, preserving first-key-wins."""

    limits = limits or ExtractionLimits()
    if len(data) > limits.max_mapping_bytes:
        raise ExtractionError("mapping file size limit exceeded")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ExtractionError("mapping XML declarations and entities are forbidden")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ExtractionError("mapping XML is malformed") from error
    values: dict[int, str] = {}
    duplicates = conflicts = ignored = 0
    for row in root.iter():
        children = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
            for child in row
        }
        if "InpageDec" not in children or "UnicodeDec" not in children:
            continue
        if children.get("Ignore", "").upper() in {"T", "TRUE", "1"}:
            ignored += 1
            continue
        try:
            code = int(children["InpageDec"])
            character = chr(int(children["UnicodeDec"]))
        except (ValueError, OverflowError) as error:
            raise ExtractionError("mapping XML contains an invalid integer") from error
        if not 0 <= code <= 255:
            raise ExtractionError("mapping byte is outside 0..255")
        if code in values:
            duplicates += 1
            conflicts += values[code] != character
            continue
        values[code] = character
        if len(values) > limits.max_mapping_entries:
            raise ExtractionError("mapping entry limit exceeded")
    return MappingTable(
        sha256(data), len(data), len(values), duplicates, conflicts, ignored, values
    )


def compare_mappings(left: MappingTable, right: MappingTable) -> MappingComparison:
    """Compare mapping values without emitting mapped source text."""

    overlap = set(left.values) & set(right.values)
    conflicting = tuple(
        sorted(code for code in overlap if left.values[code] != right.values[code])
    )
    return MappingComparison(
        left_sha256=left.source_sha256,
        right_sha256=right.source_sha256,
        overlap=len(overlap),
        agreements=len(overlap) - len(conflicting),
        conflicts=len(conflicting),
        left_only=len(set(left.values) - set(right.values)),
        right_only=len(set(right.values) - set(left.values)),
        conflicting_codes=conflicting,
    )
