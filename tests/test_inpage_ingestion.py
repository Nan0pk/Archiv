from __future__ import annotations

import json
import struct
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app
from archiv.hashing import sha256_file
from archiv.ingestion.normalizers import MalformedInputError, normalize

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


def _cfb_with_stream(name: str, payload: bytes) -> bytes:
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
        start=2 + doc_sectors,
        size=len(payload),
    )

    fat = [FREESECT] * 128
    fat[0] = ENDOFCHAIN
    fat[1] = FATSECT
    for sector in range(2, 2 + doc_sectors):
        fat[sector] = sector + 1 if sector < 1 + doc_sectors else ENDOFCHAIN
    start = 2 + doc_sectors
    for sector in range(start, start + native_sectors):
        fat[sector] = sector + 1 if sector < start + native_sectors - 1 else ENDOFCHAIN
    body = bytearray(directory + struct.pack("<128I", *fat) + document + payload)
    assert len(body) == total_sectors * 512
    return bytes(header + body)


def _legacy_units(text: str) -> bytes:
    reverse = {
        "ا": 0x81,
        "پ": 0x83,
        "ت": 0x84,
        "د": 0x8B,
        "ر": 0x8E,
        "س": 0x92,
        "ط": 0x96,
        "ک": 0x9C,
        "ن": 0xA0,
        "و": 0xA2,
        "ی": 0xA4,
    }
    output = bytearray()
    for character in text:
        if character == "\n":
            output.extend((0x0D, 0x00))
        elif character in reverse:
            output.extend((reverse[character], 0x04))
        else:
            output.extend((ord(character), 0x04))
    return bytes(output)


def test_inpage300_ingests_rebuilds_and_searches(tmp_path: Path) -> None:
    runner = CliRunner()
    home = tmp_path / "archiv-home"
    source = tmp_path / "modern.inp"
    marker = "ARCHIV-INPAGE300-MARKER"
    source.write_bytes(
        _cfb_with_stream(
            "InPage300",
            f"{marker} اردو دستاویز\r\nدوسری سطر".encode("utf-16le"),
        )
    )
    original_digest = sha256_file(source)

    ingestion = runner.invoke(app, ["ingest", str(source), "--home", str(home)])
    assert ingestion.exit_code == 0, ingestion.output
    payload = json.loads(ingestion.output)
    assert payload["media_type"] == "application/x-inpage"
    assert payload["source_hash_unchanged"] is True
    assert payload["object_sha256"] == original_digest
    normalized_path = Path(payload["derived_root"]) / "normalized" / "document.json"
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert normalized["kind"] == "inp"
    assert normalized["metadata"]["native_variant"] == "300"
    assert normalized["metadata"]["native_text_extracted"] is True
    assert any(marker in segment["text"] for segment in normalized["segments"])

    rebuild = runner.invoke(
        app,
        ["rebuild-derived", original_digest, "--home", str(home)],
    )
    assert rebuild.exit_code == 0, rebuild.output
    assert sha256_file(source) == original_digest

    index = runner.invoke(app, ["rebuild-search-index", "--home", str(home)])
    assert index.exit_code == 0, index.output
    result = runner.invoke(app, ["search", marker, "--home", str(home)])
    assert result.exit_code == 0, result.output
    matches = json.loads(result.output)
    assert len(matches) == 1
    assert matches[0]["citation"]["kind"] == "inp"
    assert matches[0]["citation"]["locator"]["stream"] == "InPage300"


def test_inpage100_ingests_legacy_urdu_and_english(tmp_path: Path) -> None:
    source = tmp_path / "legacy.inp"
    marker = "ARCHIV-INPAGE100-MARKER"
    source.write_bytes(
        _cfb_with_stream(
            "InPage100",
            _legacy_units(f"{marker} پاکستان\nدوسری سطر"),
        )
    )
    document = normalize(source, sha256_file(source))
    assert document.kind == "inp"
    assert document.metadata["native_variant"] == "100"
    text = "\n".join(segment.text for segment in document.segments)
    assert marker in text
    assert "پاکستان" in text
    assert "دوسری سطر" in text
    assert document.metadata["layout_supported"] is False


def test_inpage_rejects_unrelated_or_unknown_native_input(tmp_path: Path) -> None:
    unrelated = tmp_path / "not-inpage.inp"
    unrelated.write_bytes(b"not a compound document")
    try:
        normalize(unrelated, sha256_file(unrelated))
    except MalformedInputError as error:
        assert "InPage" in str(error) or "CFB" in str(error)
    else:
        raise AssertionError("unrelated .inp input was accepted")

    unknown = tmp_path / "unknown.inp"
    unknown.write_bytes(_cfb_with_stream("InPage999", "اردو متن".encode("utf-16le")))
    try:
        normalize(unknown, sha256_file(unknown))
    except MalformedInputError as error:
        assert "unsupported native InPage stream variant" in str(error)
    else:
        raise AssertionError("unknown native InPage variant was accepted")
