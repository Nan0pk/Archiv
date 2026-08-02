from __future__ import annotations

import json
import struct
from pathlib import Path

from archiv.research.inpage_cfb_probe import ProbeLimits, probe_path, result_json

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD


def _directory_entry(
    name: str,
    *,
    object_type: int,
    left: int = FREESECT,
    right: int = FREESECT,
    child: int = FREESECT,
) -> bytes:
    encoded = (name + "\x00").encode("utf-16le")
    if len(encoded) > 64:
        raise ValueError(name)
    entry = bytearray(128)
    entry[: len(encoded)] = encoded
    struct.pack_into("<H", entry, 64, len(encoded))
    entry[66] = object_type
    entry[67] = 1
    struct.pack_into("<III", entry, 68, left, right, child)
    struct.pack_into("<I", entry, 116, ENDOFCHAIN)
    struct.pack_into("<Q", entry, 120, 0)
    return bytes(entry)


def _cfb(
    stream_names: list[str],
    *,
    directory_next: int = ENDOFCHAIN,
    reachable_streams: int | None = None,
) -> bytes:
    if len(stream_names) > 3:
        raise ValueError("test helper supports at most three streams")
    if reachable_streams is None:
        reachable_streams = len(stream_names)
    if reachable_streams < 0 or reachable_streams > len(stream_names):
        raise ValueError("invalid reachable stream count")
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 3)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 0)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, ENDOFCHAIN)
    struct.pack_into("<I", header, 64, 0)
    struct.pack_into("<I", header, 68, ENDOFCHAIN)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<109I", header, 76, 1, *([FREESECT] * 108))

    directory = bytearray(512)
    child = 1 if reachable_streams else FREESECT
    directory[0:128] = _directory_entry("Root Entry", object_type=5, child=child)
    for index, stream_name in enumerate(stream_names, 1):
        right = (
            index + 1
            if index < reachable_streams
            else FREESECT
        )
        directory[index * 128 : (index + 1) * 128] = _directory_entry(
            stream_name,
            object_type=2,
            right=right,
        )

    fat = bytearray(b"\xff" * 512)
    struct.pack_into("<I", fat, 0, directory_next)
    struct.pack_into("<I", fat, 4, FATSECT)
    return bytes(header + directory + fat)


def _nested_cfb() -> bytes:
    data = bytearray(_cfb([]))
    directory = memoryview(data)[512:1024]
    directory[0:128] = _directory_entry("Root Entry", object_type=5, child=1)
    directory[128:256] = _directory_entry("Embedded", object_type=1, child=2)
    directory[256:384] = _directory_entry(
        "DocumentInfo",
        object_type=2,
        right=3,
    )
    directory[384:512] = _directory_entry("InPage100", object_type=2)
    return bytes(data)


def test_candidate_never_claims_native_support(tmp_path: Path) -> None:
    path = tmp_path / "sample.inp"
    path.write_bytes(_cfb(["DocumentInfo", "InPage100"]))

    result = probe_path(path)

    assert result.classification == "inpage_cfb_candidate"
    assert result.candidate_content_streams == ("InPage100",)
    assert result.has_document_info is True
    assert result.native_support_claimed is False
    assert result.stream_contents_read is False
    assert result.stream_names == ("DocumentInfo", "InPage100")
    assert result.error is None


def test_split_candidate_is_separate(tmp_path: Path) -> None:
    path = tmp_path / "sample.b01"
    path.write_bytes(_cfb(["DocumentInfo", "InPage200"]))

    result = probe_path(path)

    assert result.classification == "split_inpage_cfb_candidate"
    assert result.candidate_content_streams == ("InPage200",)


def test_unrelated_cfb_is_not_promoted(tmp_path: Path) -> None:
    path = tmp_path / "document.inp"
    path.write_bytes(_cfb(["WordDocument", "1Table"]))

    result = probe_path(path)

    assert result.classification == "unrelated_cfb"
    assert result.has_document_info is False
    assert result.candidate_content_streams == ()


def test_inpage_like_stream_without_document_info_is_unrelated(tmp_path: Path) -> None:
    path = tmp_path / "document.inp"
    path.write_bytes(_cfb(["InPage300"]))

    result = probe_path(path)

    assert result.classification == "unrelated_cfb"
    assert "InPage-like content stream exists without DocumentInfo." in result.warnings


def test_non_ole_inp_is_explicitly_unrelated(tmp_path: Path) -> None:
    path = tmp_path / "abaqus.inp"
    path.write_text("*Heading\nSynthetic model\n")

    result = probe_path(path)

    assert result.classification == "unrelated_inp"
    assert result.error is None


def test_non_ole_other_suffix_is_not_ole(tmp_path: Path) -> None:
    path = tmp_path / "notes.bin"
    path.write_bytes(b"not an ole document")

    result = probe_path(path)

    assert result.classification == "not_ole"


def test_size_limit_applies_before_hashing_or_parsing(tmp_path: Path) -> None:
    path = tmp_path / "large.inp"
    path.write_bytes(b"12345")

    result = probe_path(path, limits=ProbeLimits(max_file_bytes=4))

    assert result.classification == "oversize"
    assert result.file_sha256 == ""
    assert result.error == "file size limit exceeded before parsing"


def test_missing_path_is_explicit(tmp_path: Path) -> None:
    result = probe_path(tmp_path / "missing.inp")

    assert result.classification == "malformed"
    assert result.error == "input file does not exist"


def test_truncated_cfb_header_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "truncated.inp"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1"))

    result = probe_path(path)

    assert result.classification == "malformed"
    assert result.error == "truncated CFB header"


def test_directory_cycle_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cycle.inp"
    path.write_bytes(_cfb(["DocumentInfo", "InPage100"], directory_next=0))

    result = probe_path(path)

    assert result.classification == "malformed"
    assert result.error == "directory chain contains a cycle"


def test_nested_inpage_names_do_not_count_as_root_signature(tmp_path: Path) -> None:
    path = tmp_path / "nested.inp"
    path.write_bytes(_nested_cfb())

    result = probe_path(path)

    assert result.classification == "unrelated_cfb"
    assert result.stream_names == (
        "Embedded/DocumentInfo",
        "Embedded/InPage100",
    )
    assert result.has_document_info is False


def test_orphan_inpage_names_do_not_promote_unrelated_cfb(tmp_path: Path) -> None:
    path = tmp_path / "orphan-decoy.inp"
    path.write_bytes(
        _cfb(
            ["WordDocument", "DocumentInfo", "InPage100"],
            reachable_streams=1,
        )
    )

    result = probe_path(path)

    assert result.classification == "unrelated_cfb"
    assert result.stream_names == ("WordDocument",)
    assert "Ignored 2 non-empty orphan directory entries." in result.warnings


def test_output_is_deterministic_and_contains_no_stream_bytes(tmp_path: Path) -> None:
    path = tmp_path / "sample.inp"
    path.write_bytes(_cfb(["DocumentInfo", "InPage100"]))

    first = result_json(probe_path(path))
    second = result_json(probe_path(path))
    payload = json.loads(first)

    assert first == second
    assert payload["stream_contents_read"] is False
    assert "stream_content" not in payload
    assert payload["native_support_claimed"] is False


def test_duplicate_fat_sector_identifiers_are_rejected(tmp_path: Path) -> None:
    data = bytearray(_cfb(["DocumentInfo", "InPage100"]))
    struct.pack_into("<I", data, 44, 2)
    struct.pack_into("<109I", data, 76, 1, 1, *([FREESECT] * 107))
    path = tmp_path / "duplicate-fat.inp"
    path.write_bytes(data)

    result = probe_path(path)

    assert result.classification == "malformed"
    assert result.error == "duplicate FAT sector identifier"


def test_fat_sector_must_mark_itself(tmp_path: Path) -> None:
    data = bytearray(_cfb(["DocumentInfo", "InPage100"]))
    struct.pack_into("<I", data, 1024 + 4, ENDOFCHAIN)
    path = tmp_path / "bad-fat-marker.inp"
    path.write_bytes(data)

    result = probe_path(path)

    assert result.classification == "malformed"
    assert result.error == "FAT sector is not marked as FATSECT"
