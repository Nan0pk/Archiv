from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from archiv.research.inpage_container import extract_inpage300, read_native_root_stream
from archiv.research.inpage_legacy import compare_mappings, extract_inpage100, parse_mapping_xml
from archiv.research.inpage_types import ExtractionError, MappingTable, RootStream
from archiv.research.inpage_validation import (
    compare_quran_text,
    compute_git_blob_sha1,
    metrics_json,
    write_private_text,
)

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD


def _entry(
    name: str,
    *,
    object_type: int,
    right: int = FREESECT,
    child: int = FREESECT,
    start: int = ENDOFCHAIN,
    size: int = 0,
) -> bytes:
    encoded = (name + "\x00").encode("utf-16le")
    raw = bytearray(128)
    raw[: len(encoded)] = encoded
    struct.pack_into("<H", raw, 64, len(encoded))
    raw[66] = object_type
    raw[67] = 1
    struct.pack_into("<III", raw, 68, FREESECT, right, child)
    struct.pack_into("<I", raw, 116, start)
    struct.pack_into("<Q", raw, 120, size)
    return bytes(raw)


def _cfb_with_stream(name: str, payload: bytes, *, duplicate: bool = False) -> bytes:
    payload = payload + b"\x00" * max(0, 4096 - len(payload))
    document = b"D" * 4096
    doc_sectors = 8
    native_sectors = (len(payload) + 511) // 512
    total_sectors = 2 + doc_sectors + native_sectors
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHH", header, 24, 0x003E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 0)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, ENDOFCHAIN)
    struct.pack_into("<I", header, 68, ENDOFCHAIN)
    struct.pack_into("<109I", header, 76, 1, *([FREESECT] * 108))

    directory = bytearray(512)
    directory[:128] = _entry("Root Entry", object_type=5, child=1)
    directory[128:256] = _entry("DocumentInfo", object_type=2, right=2, start=2, size=len(document))
    directory[256:384] = _entry(
        name,
        object_type=2,
        right=3 if duplicate else FREESECT,
        start=2 + doc_sectors,
        size=len(payload),
    )
    if duplicate:
        directory[384:512] = _entry(name, object_type=2, start=2 + doc_sectors, size=len(payload))

    fat = [FREESECT] * 128
    fat[0] = ENDOFCHAIN
    fat[1] = FATSECT
    for sector in range(2, 2 + doc_sectors):
        fat[sector] = sector + 1 if sector < 1 + doc_sectors else ENDOFCHAIN
    start = 2 + doc_sectors
    for sector in range(start, start + native_sectors):
        fat[sector] = sector + 1 if sector < start + native_sectors - 1 else ENDOFCHAIN
    fat_bytes = struct.pack("<128I", *fat)
    body = bytearray(directory + fat_bytes + document + payload)
    expected = total_sectors * 512
    assert len(body) == expected
    return bytes(header + body)


def _root_stream(variant: str, payload: bytes) -> RootStream:
    return RootStream(
        name=f"InPage{variant}",
        variant=variant,
        stream_size=len(payload),
        stream_sha256=hashlib.sha256(payload).hexdigest(),
        sector_size=512,
        sector_count=(len(payload) + 511) // 512,
        payload=payload,
    )


def test_read_root_stream_and_reject_ambiguity(tmp_path: Path) -> None:
    path = tmp_path / "sample.inp"
    path.write_bytes(_cfb_with_stream("InPage300", "اردو text".encode("utf-16le")))
    stream = read_native_root_stream(path)
    assert stream.variant == "300"
    assert stream.stream_size == 4096

    path.write_bytes(_cfb_with_stream("InPage300", b"x", duplicate=True))
    with pytest.raises(ExtractionError, match="duplicate root stream|expected one"):
        read_native_root_stream(path)


def test_inpage300_metrics_never_emit_text() -> None:
    payload = "junk\x00اردو text\r\nمزید".encode("utf-16le")
    metrics, text = extract_inpage300(_root_stream("300", payload))
    serialized = metrics_json(metrics)
    assert "اردو" in text
    assert "اردو" not in serialized
    assert metrics.native_support_claimed is False
    arabic_units = metrics.details["arabic_code_units"]
    assert isinstance(arabic_units, int)
    assert arabic_units >= 8


def test_inpage100_measures_both_record_lengths() -> None:
    record = b"\x04\x81abc\r"
    payload = struct.pack("<I", len(record)) + record
    mapping = MappingTable("a" * 64, 1, 1, 0, 0, 0, {0x81: "ا"})
    metrics, text = extract_inpage100(_root_stream("100", payload), mapping=mapping)
    assert text == "اabc"
    assert metrics.details["plausible_u32_length_records"] == 1
    assert metrics.details["plausible_u16_length_records"] == 1
    assert metrics.details["shared_record_offsets"] == 1


def test_nonzero_upper_word_exposes_framing_disagreement() -> None:
    record = b"abc\r"
    payload = struct.pack("<HH", len(record), 1) + record
    metrics, _ = extract_inpage100(_root_stream("100", payload))
    assert metrics.details["plausible_u32_length_records"] == 0
    assert metrics.details["plausible_u16_length_records"] == 1
    nonzero_upper = metrics.details["nonzero_upper_length_words"]
    assert isinstance(nonzero_upper, int)
    assert nonzero_upper >= 1


def test_mapping_first_key_wins_and_comparison() -> None:
    left = parse_mapping_xml(
        b"<R><X><InpageDec>129</InpageDec><UnicodeDec>1575</UnicodeDec></X>"
        b"<X><InpageDec>129</InpageDec><UnicodeDec>1576</UnicodeDec></X></R>"
    )
    right = MappingTable("b" * 64, 1, 1, 0, 0, 0, {129: "ب"})
    comparison = compare_mappings(left, right)
    assert left.values[129] == "ا"
    assert left.duplicates == 1
    assert left.conflicts == 1
    assert comparison.conflicting_codes == (129,)


def test_private_output_is_exclusive_and_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "private.txt"
    write_private_text(path, "secret")
    assert path.read_text() == "secret"
    assert os.stat(path).st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private_text(path, "replacement")


def test_quran_comparison_modes_and_blob_identity() -> None:
    result = compare_quran_text("بِسْمِ  الله", "بسم الله", mode="diacritic_insensitive")
    assert result.exact_match is True
    assert result.matching_ratio == 1.0
    data = b"abc"
    assert compute_git_blob_sha1(data) == hashlib.sha1(b"blob 3\0abc").hexdigest()
    assert "بسم" not in json.dumps(result.__dict__, ensure_ascii=False)
