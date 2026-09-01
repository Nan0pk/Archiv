"""Legacy binary Microsoft Office ingestion: extraction and fail-closed safety.

Covers ``.xls`` (BIFF8), ``.doc`` (MS-DOC), and ``.ppt`` (MS-PPT). The ``.doc``
and ``.ppt`` fixtures are hand-built, spec-accurate synthetic instances (FIB +
Clx/PlcPcd piece table; MS-PPT record tree) — lawful, self-authored, and
exercising the real parsing logic, mirroring this repo's existing convention
of generating fixtures from constants rather than sourcing real documents
(see tests/fixtures/README.md).
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
LEGACY_MARKER = "ARCHIV-LEGACY-OFFICE-MARKER"

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


def _cfb_container(streams: dict[str, bytes]) -> bytes:
    """Build a minimal multi-stream CFB container (root + up to 3 sibling streams)."""

    assert 1 <= len(streams) <= 3
    padded = {
        name: (data if len(data) >= 4096 else data + b"\x00" * (4096 - len(data)))
        for name, data in streams.items()
    }
    directory = bytearray(512)
    directory[:128] = _cfb_entry("Root Entry", object_type=5, child=1)
    sector = 2
    body = bytearray()
    names = list(padded)
    for index, name in enumerate(names, start=1):
        data = padded[name]
        sectors = len(data) // 512
        right = index + 1 if index < len(names) else _FREESECT
        # Declared size must be >= the mini-stream cutoff (4096) or the CFB reader
        # expects mini-FAT allocation instead of the regular FAT chain below.
        directory[index * 128 : (index + 1) * 128] = _cfb_entry(
            name, object_type=2, right=right, start=sector, size=len(data)
        )
        body.extend(data)
        sector += sectors

    fat_len = ((sector + 127) // 128) * 128
    fat = [_FREESECT] * fat_len
    fat[0] = _ENDOFCHAIN
    fat[1] = _FATSECT
    cursor = 2
    for name in names:
        sectors = len(padded[name]) // 512
        for offset in range(sectors):
            fat[cursor + offset] = cursor + offset + 1 if offset < sectors - 1 else _ENDOFCHAIN
        cursor += sectors

    return bytes(_cfb_header() + directory + struct.pack(f"<{fat_len}I", *fat) + body)


# --- .doc fixtures ---

_DOC_MEDIA_TYPE = "application/msword"


def _doc_streams(*, text: str, encrypted: bool = False) -> dict[str, bytes]:
    text_bytes = text.encode("utf-16-le")
    word_document = bytearray(1024)
    struct.pack_into("<H", word_document, 0, 0xA5EC)  # wIdent
    struct.pack_into("<H", word_document, 2, 0x00C1)  # nFib = Word 97
    flags1 = 0x0100 if encrypted else 0x0000  # fEncrypted
    struct.pack_into("<H", word_document, 10, flags1)
    struct.pack_into("<H", word_document, 32, 14)  # csw
    struct.pack_into("<H", word_document, 62, 22)  # cslw
    struct.pack_into("<H", word_document, 152, 34)  # cbRgFcLcb
    text_offset = 512
    word_document[text_offset : text_offset + len(text_bytes)] = text_bytes

    cp_values = struct.pack("<II", 0, len(text))
    pcd = struct.pack("<HIH", 0, text_offset, 0)  # flags, FcCompressed(fCompressed=0), prm
    plc_pcd = cp_values + pcd
    clx = bytes([0x02]) + struct.pack("<I", len(plc_pcd)) + plc_pcd

    fc_clx_offset = 154 + 33 * 8
    struct.pack_into("<II", word_document, fc_clx_offset, 0, len(clx))

    return {"WordDocument": bytes(word_document), "0Table": clx}


def _write_doc(path: Path, *, text: str = f"Archiv DOC ingestion fixture {LEGACY_MARKER}\r") -> None:
    path.write_bytes(_cfb_container(_doc_streams(text=text)))


def _write_encrypted_doc(path: Path) -> None:
    path.write_bytes(_cfb_container(_doc_streams(text="irrelevant\r", encrypted=True)))


def _write_doc_with_macro_stream(path: Path) -> None:
    streams = _doc_streams(text=f"Archiv DOC ingestion fixture {LEGACY_MARKER}\r")
    streams["_VBA_PROJECT_CUR"] = b"MACRO-PAYLOAD-SHOULD-NEVER-BE-READ-OR-EXECUTED" * 20
    path.write_bytes(_cfb_container(streams))


# --- .ppt fixtures ---

_PPT_MEDIA_TYPE = "application/vnd.ms-powerpoint"


def _ppt_record(rec_type: int, payload: bytes, *, container: bool = False) -> bytes:
    ver_instance = 0x000F if container else 0x0000
    return struct.pack("<HHI", ver_instance, rec_type, len(payload)) + payload


def _ppt_stream(*, text: str) -> bytes:
    text_atom = _ppt_record(0x0FA0, text.encode("utf-16-le"))
    slide = _ppt_record(0x03EE, text_atom, container=True)
    return _ppt_record(0x03E8, slide, container=True)


def _write_ppt(path: Path, *, text: str = f"Archiv PPT ingestion fixture {LEGACY_MARKER}") -> None:
    path.write_bytes(_cfb_container({"PowerPoint Document": _ppt_stream(text=text)}))


def _write_encrypted_ppt(path: Path) -> None:
    crypt_session = _ppt_record(0x2F14, b"", container=True)
    path.write_bytes(_cfb_container({"PowerPoint Document": crypt_session}))


def _write_ppt_with_macro_stream(path: Path) -> None:
    streams = {
        "PowerPoint Document": _ppt_stream(text=f"Archiv PPT ingestion fixture {LEGACY_MARKER}"),
        "_VBA_PROJECT_CUR": b"MACRO-PAYLOAD-SHOULD-NEVER-BE-READ-OR-EXECUTED" * 20,
    }
    path.write_bytes(_cfb_container(streams))


# --- .xls fixtures ---


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


_XLS_MEDIA_TYPE = "application/vnd.ms-excel"


# --- .doc tests ---


def test_doc_extracts_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "document.doc"
    _write_doc(path)

    document = normalize(path, DIGEST)

    assert document.kind == "doc"
    assert document.media_type == _DOC_MEDIA_TYPE
    assert len(document.segments) == 1
    assert document.segments[0].locator == {"paragraph": 1}
    assert document.segments[0].text == f"Archiv DOC ingestion fixture {LEGACY_MARKER}"
    assert document.metadata["macros_executed"] is False
    assert document.metadata["processor"] == "archiv.legacy-office-doc"


def test_doc_splits_multiple_paragraphs_and_decodes_compressed_text(tmp_path: Path) -> None:
    # Two pieces: one uncompressed (UTF-16LE), one compressed (CP1252) — exercising
    # both FcCompressed branches, with a paragraph mark between them.
    path = tmp_path / "two-piece.doc"
    first = "First paragraph"
    second = f"Second {LEGACY_MARKER}"
    first_bytes = first.encode("utf-16-le") + "\r".encode("utf-16-le")
    second_bytes = second.encode("cp1252")

    word_document = bytearray(1024)
    struct.pack_into("<H", word_document, 0, 0xA5EC)
    struct.pack_into("<H", word_document, 2, 0x00C1)
    struct.pack_into("<H", word_document, 32, 14)
    struct.pack_into("<H", word_document, 62, 22)
    struct.pack_into("<H", word_document, 152, 34)
    first_offset = 512
    second_offset = 700  # even offset; the real byte position for compressed text is fc // 2
    word_document[first_offset : first_offset + len(first_bytes)] = first_bytes
    word_document[second_offset : second_offset + len(second_bytes)] = second_bytes

    cp0, cp1, cp2 = 0, len(first) + 1, len(first) + 1 + len(second)
    cp_values = struct.pack("<III", cp0, cp1, cp2)
    pcd0 = struct.pack("<HIH", 0, first_offset, 0)  # uncompressed: fc is a direct byte offset
    pcd1 = struct.pack("<HIH", 0, (second_offset * 2) | 0x4000_0000, 0)  # compressed: fc/2 = offset
    plc_pcd = cp_values + pcd0 + pcd1
    clx = bytes([0x02]) + struct.pack("<I", len(plc_pcd)) + plc_pcd
    fc_clx_offset = 154 + 33 * 8
    struct.pack_into("<II", word_document, fc_clx_offset, 0, len(clx))

    path.write_bytes(_cfb_container({"WordDocument": bytes(word_document), "0Table": clx}))

    document = normalize(path, DIGEST)

    assert [(s.locator, s.text) for s in document.segments] == [
        ({"paragraph": 1}, first),
        ({"paragraph": 2}, second),
    ]
    assert document.metadata["piece_count"] == 2


def test_doc_rejects_encrypted_document(tmp_path: Path) -> None:
    path = tmp_path / "protected.doc"
    _write_encrypted_doc(path)

    with pytest.raises(MalformedInputError, match="encrypted"):
        normalize(path, DIGEST)


def test_doc_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.doc"
    path.write_bytes(b"not an OLE2 container, just noise 0123456789")

    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)


def test_doc_never_reads_macro_project_stream(tmp_path: Path) -> None:
    path = tmp_path / "with-macro.doc"
    _write_doc_with_macro_stream(path)

    document = normalize(path, DIGEST)

    texts = {segment.text for segment in document.segments}
    assert any(LEGACY_MARKER in text for text in texts)
    serialized = document.model_dump_json()
    assert "MACRO-PAYLOAD" not in serialized
    assert document.metadata["macros_executed"] is False


def test_doc_complete_immutable_ingestion_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "lifecycle.doc"
    _write_doc(source)
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
    assert payload["kind"] == "doc"
    assert payload["metadata"]["macros_executed"] is False

    renamed = tmp_path / "renamed.doc"
    renamed.write_bytes(source_before)
    second = ingest_file(renamed, home=home)
    assert second.duplicate is True
    assert second.object_sha256 == first.object_sha256

    shutil.rmtree(first.derived_root)
    evidence = rebuild_derived(first.object_sha256, home=home)
    assert evidence
    assert sha256_file(original) == original_hash
    assert sha256_file(normalized) == normalized_hash


# --- .xls tests ---


def test_xls_extracts_cells_and_builds_table(tmp_path: Path) -> None:
    path = tmp_path / "workbook.xls"
    _write_xls(path)

    document = normalize(path, DIGEST)

    assert document.kind == "xls"
    assert document.media_type == _XLS_MEDIA_TYPE
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


# --- .ppt tests ---


def test_ppt_extracts_slide_text(tmp_path: Path) -> None:
    path = tmp_path / "presentation.ppt"
    _write_ppt(path)

    document = normalize(path, DIGEST)

    assert document.kind == "ppt"
    assert document.media_type == _PPT_MEDIA_TYPE
    assert len(document.segments) == 1
    assert document.segments[0].locator == {"slide": 1, "shape": 1}
    assert document.segments[0].text == f"Archiv PPT ingestion fixture {LEGACY_MARKER}"
    assert document.metadata["macros_executed"] is False
    assert document.metadata["processor"] == "archiv.legacy-office-ppt"


def test_ppt_decodes_text_bytes_atom_and_numbers_slides(tmp_path: Path) -> None:
    # Two slides: one with a TextCharsAtom (UTF-16LE), one with a TextBytesAtom (CP1252).
    path = tmp_path / "two-slide.ppt"
    chars_text = "Slide one text"
    bytes_text = f"Slide two {LEGACY_MARKER}"
    slide_one = _ppt_record(
        0x03EE, _ppt_record(0x0FA0, chars_text.encode("utf-16-le")), container=True
    )
    slide_two = _ppt_record(
        0x03EE, _ppt_record(0x0FA8, bytes_text.encode("cp1252")), container=True
    )
    document_stream = _ppt_record(0x03E8, slide_one + slide_two, container=True)
    path.write_bytes(_cfb_container({"PowerPoint Document": document_stream}))

    document = normalize(path, DIGEST)

    assert [(s.locator, s.text) for s in document.segments] == [
        ({"slide": 1, "shape": 1}, chars_text),
        ({"slide": 2, "shape": 1}, bytes_text),
    ]


def test_ppt_rejects_encrypted_presentation(tmp_path: Path) -> None:
    path = tmp_path / "protected.ppt"
    _write_encrypted_ppt(path)

    with pytest.raises(MalformedInputError, match="encrypted"):
        normalize(path, DIGEST)


def test_ppt_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.ppt"
    path.write_bytes(b"not an OLE2 container, just noise 0123456789")

    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)


def test_ppt_never_reads_macro_project_stream(tmp_path: Path) -> None:
    path = tmp_path / "with-macro.ppt"
    _write_ppt_with_macro_stream(path)

    document = normalize(path, DIGEST)

    texts = {segment.text for segment in document.segments}
    assert any(LEGACY_MARKER in text for text in texts)
    serialized = document.model_dump_json()
    assert "MACRO-PAYLOAD" not in serialized
    assert document.metadata["macros_executed"] is False


def test_ppt_complete_immutable_ingestion_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "lifecycle.ppt"
    _write_ppt(source)
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
    assert payload["kind"] == "ppt"
    assert payload["metadata"]["macros_executed"] is False

    renamed = tmp_path / "renamed.ppt"
    renamed.write_bytes(source_before)
    second = ingest_file(renamed, home=home)
    assert second.duplicate is True
    assert second.object_sha256 == first.object_sha256

    shutil.rmtree(first.derived_root)
    evidence = rebuild_derived(first.object_sha256, home=home)
    assert evidence
    assert sha256_file(original) == original_hash
    assert sha256_file(normalized) == normalized_hash
