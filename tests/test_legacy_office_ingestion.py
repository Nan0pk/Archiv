"""Legacy binary Excel (.xls) ingestion: extraction, safety, and fail-closed behavior.

Only ``.xls`` is covered here. ``.doc`` and ``.ppt`` remain unsupported: this
project's own field-trial policy (``docs/format-compatibility.json``
``field_trial_decision``) gates ``.doc``/``.rtf`` behind acquiring lawful
content-bearing fixtures first, and there is no trustworthy way to construct or
verify a binary Word/PowerPoint fixture without one.
"""

from __future__ import annotations

import datetime as _datetime
import json
import shutil
import struct
from io import BytesIO
from pathlib import Path

import olefile
import pytest
import xlwt
from xlwt import Workbook as LegacyWorkbook

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file, rebuild_derived
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
MARKER = "ARCHIV-XLS-MARKER"

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD


def _cfb_entry(
    name: str,
    *,
    object_type: int,
    right: int = _FREESECT,
    child: int = _FREESECT,
    start: int = _ENDOFCHAIN,
    size: int = 0,
) -> bytes:
    encoded = (name + "\x00").encode("utf-16le")
    raw = bytearray(128)
    raw[: len(encoded)] = encoded
    struct.pack_into("<H", raw, 64, len(encoded))
    raw[66] = object_type
    raw[67] = 1
    struct.pack_into("<III", raw, 68, _FREESECT, right, child)
    struct.pack_into("<I", raw, 116, start)
    struct.pack_into("<Q", raw, 120, size)
    return bytes(raw)


def _pad(data: bytes, boundary: int = 512) -> bytes:
    remainder = len(data) % boundary
    return data if remainder == 0 else data + b"\x00" * (boundary - remainder)


def _cfb_header() -> bytearray:
    header = bytearray(512)
    header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    struct.pack_into("<HHHH", header, 24, 0x003E, 3, 0xFFFE, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 0)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, _ENDOFCHAIN)
    struct.pack_into("<I", header, 68, _ENDOFCHAIN)
    struct.pack_into("<109I", header, 76, 1, *([_FREESECT] * 108))
    return header


def _single_stream_cfb(name: str, payload: bytes) -> bytes:
    """Build a minimal one-stream CFB container, mirroring the InPage test fixtures."""

    data = _pad(payload, 4096) if len(payload) < 4096 else _pad(payload)
    sectors = len(data) // 512
    directory = bytearray(512)
    directory[:128] = _cfb_entry("Root Entry", object_type=5, child=1)
    # The declared stream size must be >= the mini-stream cutoff (4096) or the CFB
    # reader will expect mini-FAT allocation instead of the regular FAT chain below.
    directory[128:256] = _cfb_entry(name, object_type=2, start=2, size=len(data))
    fat = [_FREESECT] * 128
    fat[0] = _ENDOFCHAIN
    fat[1] = _FATSECT
    for sector in range(2, 2 + sectors):
        fat[sector] = sector + 1 if sector < 1 + sectors else _ENDOFCHAIN
    body = bytearray(directory + struct.pack("<128I", *fat) + data)
    return bytes(_cfb_header() + body)


def _real_workbook_bytes(*, marker_cell: tuple[int, int] = (1, 1)) -> bytes:
    workbook = LegacyWorkbook()
    sheet = workbook.add_sheet("Evidence")
    sheet.write(0, 0, "Archiv XLS ingestion fixture")
    sheet.write(*marker_cell, MARKER)
    raw = BytesIO()
    workbook.save(raw)
    return raw.getvalue()


def _write_xls(path: Path) -> None:
    path.write_bytes(_real_workbook_bytes())


def _write_encrypted_xls(path: Path) -> None:
    def record(record_type: int, payload: bytes) -> bytes:
        return struct.pack("<HH", record_type, len(payload)) + payload

    bof = record(0x0809, struct.pack("<HHHH", 0x0600, 0x0005, 0, 0))
    filepass = record(0x002F, b"\x01\x00" + b"\x00" * 4)
    eof = record(0x000A, b"")
    path.write_bytes(_single_stream_cfb("Workbook", bof + filepass + eof))


def _write_xls_with_macro_stream(path: Path) -> None:
    workbook_bytes = _real_workbook_bytes()
    ole: olefile.OleFileIO[str] = olefile.OleFileIO(BytesIO(workbook_bytes))
    try:
        workbook_stream = ole.openstream("Workbook").read()
    finally:
        ole.close()

    macro_payload = b"MACRO-PAYLOAD-SHOULD-NEVER-BE-READ-OR-EXECUTED" * 20
    wb_data = _pad(workbook_stream, 4096) if len(workbook_stream) < 4096 else _pad(workbook_stream)
    vba_data = _pad(macro_payload, 4096) if len(macro_payload) < 4096 else _pad(macro_payload)
    wb_sectors = len(wb_data) // 512
    vba_sectors = len(vba_data) // 512

    directory = bytearray(512)
    directory[:128] = _cfb_entry("Root Entry", object_type=5, child=1)
    directory[128:256] = _cfb_entry(
        "Workbook", object_type=2, right=2, start=2, size=len(workbook_stream)
    )
    directory[256:384] = _cfb_entry(
        "_VBA_PROJECT_CUR",
        object_type=2,
        start=2 + wb_sectors,
        size=len(macro_payload),
    )
    fat = [_FREESECT] * 128
    fat[0] = _ENDOFCHAIN
    fat[1] = _FATSECT
    for sector in range(2, 2 + wb_sectors):
        fat[sector] = sector + 1 if sector < 1 + wb_sectors else _ENDOFCHAIN
    start = 2 + wb_sectors
    for sector in range(start, start + vba_sectors):
        fat[sector] = sector + 1 if sector < start + vba_sectors - 1 else _ENDOFCHAIN
    body = bytearray(directory + struct.pack("<128I", *fat) + wb_data + vba_data)
    path.write_bytes(bytes(_cfb_header() + body))


MEDIA_TYPE = "application/vnd.ms-excel"


def test_xls_extracts_cells_and_builds_table(tmp_path: Path) -> None:
    path = tmp_path / "workbook.xls"
    _write_xls(path)

    document = normalize(path, DIGEST)

    assert document.kind == "xls"
    assert document.media_type == MEDIA_TYPE
    texts = {(segment.locator["cell"], segment.text) for segment in document.segments}
    assert ("A1", "Archiv XLS ingestion fixture") in texts
    assert ("B2", MARKER) in texts
    for segment in document.segments:
        assert set(segment.locator) == {"sheet", "cell"}
        assert segment.locator["sheet"] == "Evidence"

    assert len(document.tables) == 1
    assert document.tables[0].locator == {"sheet": "Evidence"}
    assert document.metadata["macros_executed"] is False
    assert document.metadata["formulas_decompiled"] is False
    assert document.metadata["sheets"] == ["Evidence"]
    assert document.metadata["processor"] == "archiv.legacy-office-xls"


def test_xls_maps_numeric_boolean_date_and_error_cells(tmp_path: Path) -> None:
    path = tmp_path / "types.xls"
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("Types")
    sheet.write(0, 0, 3.5)
    sheet.write(1, 0, True)
    date_style = xlwt.XFStyle()
    date_style.num_format_str = "YYYY-MM-DD"
    sheet.write(2, 0, _datetime.datetime(2024, 1, 15), date_style)
    raw = BytesIO()
    workbook.save(raw)
    path.write_bytes(raw.getvalue())

    document = normalize(path, DIGEST)
    by_cell = {segment.locator["cell"]: segment.text for segment in document.segments}
    assert by_cell["A1"] == "3.5"
    assert by_cell["A2"] == "True"
    assert by_cell["A3"].startswith("2024-01-15")


def test_xls_rejects_encrypted_workbook(tmp_path: Path) -> None:
    path = tmp_path / "protected.xls"
    _write_encrypted_xls(path)

    with pytest.raises(MalformedInputError, match="encrypted"):
        normalize(path, DIGEST)


def test_xls_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.xls"
    path.write_bytes(b"not an OLE2 container, just noise 0123456789")

    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)


def test_xls_never_reads_macro_project_stream(tmp_path: Path) -> None:
    path = tmp_path / "with-macro.xls"
    _write_xls_with_macro_stream(path)

    document = normalize(path, DIGEST)

    texts = {segment.text for segment in document.segments}
    assert MARKER in texts
    serialized = document.model_dump_json()
    assert "MACRO-PAYLOAD" not in serialized
    assert document.metadata["macros_executed"] is False


def test_xls_complete_immutable_ingestion_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "lifecycle.xls"
    _write_xls(source)
    source_before = source.read_bytes()
    home = tmp_path / "archiv-home"

    first = ingest_file(source, home=home)
    assert first.status == "succeeded"
    assert first.source_hash_unchanged is True
    assert source.read_bytes() == source_before
    original = Path(first.original_path)
    original_hash = sha256_file(original)
    normalized = Path(first.derived_root) / "normalized" / "document.json"
    normalized_hash = sha256_file(normalized)
    payload = json.loads(normalized.read_text(encoding="utf-8"))
    assert payload["kind"] == "xls"
    assert payload["metadata"]["macros_executed"] is False

    renamed = tmp_path / "renamed.xls"
    renamed.write_bytes(source_before)
    second = ingest_file(renamed, home=home)
    assert second.duplicate is True
    assert second.object_sha256 == first.object_sha256
    assert second.original_path == first.original_path

    shutil.rmtree(first.derived_root)
    evidence = rebuild_derived(first.object_sha256, home=home)
    assert evidence
    assert sha256_file(original) == original_hash
    assert sha256_file(normalized) == normalized_hash
