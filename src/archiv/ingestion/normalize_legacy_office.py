"""Legacy binary Microsoft Office normalization: ``.doc`` and ``.ppt``.

``.doc`` (Word 97-2003) parsing follows [MS-DOC]: the FIB (File Information
Block) in the "WordDocument" stream locates the Clx/PlcPcd piece table in the
table stream ("0Table" or "1Table"), and each piece's FcCompressed field gives
the byte offset and character encoding (UTF-16LE or CP1252) of its text run in
the WordDocument stream. Only these three named streams are ever opened; a VBA
project storage (macros) is never read, so macros cannot execute. Documents
with FibBase's fEncrypted bit set fail closed before any text is read.

``.ppt`` (PowerPoint 97-2003) parsing follows [MS-PPT]: the "PowerPoint
Document" stream is a tree of length-prefixed records; text lives in
TextCharsAtom (UTF-16LE) and TextBytesAtom (8-bit) atoms inside Slide
containers. A CryptSession10Container record anywhere in the stream fails
closed as encrypted. Only the "PowerPoint Document" stream is ever opened.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import olefile

from archiv.contracts import NormalizedDocument, NormalizedSegment

DOC_PROCESSOR_VERSION = "1"
PPT_PROCESSOR_VERSION = "1"

# --- .doc (MS-DOC) ---

_WORD_IDENT = 0xA5EC
_NFIB_MIN = 0x00C1  # Word 97
_NFIB_MAX = 0x0112  # Word 2003 (enhanced)
_FLAGS1_OFFSET = 10
_F_ENCRYPTED = 0x0100
_F_WHICH_TBL_STM = 0x0200
_FIB_RG_FC_LCB_START = 154
_FC_CLX_PAIR_INDEX = 33  # stable across nFib 97-2003; later versions only append fields
_MAX_WORD_DOCUMENT_BYTES = 256 * 1024 * 1024
_MAX_TABLE_STREAM_BYTES = 64 * 1024 * 1024
_MAX_PRC_ENTRIES = 64
_MAX_PIECES = 100_000
_MAX_DOC_TEXT_CHARACTERS = 20_000_000


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _fib_flags1(word_document: bytes) -> int:
    if len(word_document) < 32:
        raise ValueError("WordDocument stream is too small for a FIB header")
    if _u16(word_document, 0) != _WORD_IDENT:
        raise ValueError("WordDocument stream does not start with the FIB magic")
    nfib = _u16(word_document, 2)
    if not (_NFIB_MIN <= nfib <= _NFIB_MAX):
        raise ValueError(f"unsupported DOC FIB version: {nfib:#06x}")
    return _u16(word_document, _FLAGS1_OFFSET)


def _fc_clx(word_document: bytes) -> tuple[int, int, bool]:
    flags1 = _fib_flags1(word_document)
    if flags1 & _F_ENCRYPTED:
        raise ValueError("encrypted DOC documents are not supported")
    pair_offset = _FIB_RG_FC_LCB_START + _FC_CLX_PAIR_INDEX * 8
    if len(word_document) < pair_offset + 8:
        raise ValueError("FIB fibRgFcLcb97 is too small to contain fcClx/lcbClx")
    fc_clx = _u32(word_document, pair_offset)
    lcb_clx = _u32(word_document, pair_offset + 4)
    if lcb_clx == 0:
        raise ValueError("DOC document has no Clx (piece table)")
    which_table = bool(flags1 & _F_WHICH_TBL_STM)
    return fc_clx, lcb_clx, which_table


def _piece_runs(clx: bytes) -> list[tuple[int, bool, int]]:
    """Walk the Clx to the Pcdt/PlcPcd and return (fc, is_compressed, char_count) per piece."""

    offset = 0
    prc_entries = 0
    while True:
        if offset >= len(clx):
            raise ValueError("Clx ended before a Pcdt was found")
        clxt = clx[offset]
        if clxt == 0x02:
            break
        prc_entries += 1
        if prc_entries > _MAX_PRC_ENTRIES:
            raise ValueError("Clx RgPrc has too many entries")
        # A Prc's own contents aren't needed for text extraction, but it still has to
        # be skippable: clxt(1) + cbGrpprl(2) + that many bytes of property data.
        if offset + 3 > len(clx):
            raise ValueError("Clx Prc entry is truncated")
        cb_grpprl = _u16(clx, offset + 1)
        offset += 3 + cb_grpprl

    if offset + 5 > len(clx):
        raise ValueError("Clx Pcdt header is truncated")
    lcb = _u32(clx, offset + 1)
    plc_start = offset + 5
    if lcb < 4 or (lcb - 4) % 12 != 0:
        raise ValueError("PlcPcd has an invalid length")
    piece_count = (lcb - 4) // 12
    if piece_count == 0:
        raise ValueError("DOC document has no text pieces")
    if piece_count > _MAX_PIECES:
        raise ValueError("DOC piece count limit exceeded")
    if plc_start + lcb > len(clx):
        raise ValueError("PlcPcd extends past the end of the Clx")

    cp_array_offset = plc_start
    pcd_array_offset = plc_start + 4 * (piece_count + 1)
    cp_values = [_u32(clx, cp_array_offset + index * 4) for index in range(piece_count + 1)]

    runs: list[tuple[int, bool, int]] = []
    for index in range(piece_count):
        char_count = cp_values[index + 1] - cp_values[index]
        if char_count < 0:
            raise ValueError("Clx CP array is not monotonically increasing")
        fc_compressed = _u32(clx, pcd_array_offset + index * 8 + 2)
        is_compressed = bool(fc_compressed & 0x4000_0000)
        fc = fc_compressed & 0x3FFF_FFFF
        runs.append((fc, is_compressed, char_count))
    return runs


def _piece_text(word_document: bytes, *, fc: int, is_compressed: bool, char_count: int) -> str:
    if is_compressed:
        start = fc // 2
        end = start + char_count
        if end > len(word_document):
            raise ValueError("DOC compressed piece extends past the WordDocument stream")
        return word_document[start:end].decode("cp1252", errors="strict")
    start = fc
    end = start + char_count * 2
    if end > len(word_document):
        raise ValueError("DOC uncompressed piece extends past the WordDocument stream")
    return word_document[start:end].decode("utf-16-le", errors="strict")


def normalize_doc(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Validate and normalize one Word 97-2003 (binary) document without execution."""

    if path.stat().st_size > _MAX_WORD_DOCUMENT_BYTES:
        raise ValueError("DOC package size limit exceeded")

    ole: olefile.OleFileIO[str] = olefile.OleFileIO(str(path))
    try:
        word_document = ole.openstream("WordDocument").read()
        fc_clx, lcb_clx, which_table = _fc_clx(word_document)
        table_stream = ole.openstream("1Table" if which_table else "0Table").read()
    finally:
        ole.close()

    if len(table_stream) > _MAX_TABLE_STREAM_BYTES:
        raise ValueError("DOC table stream size limit exceeded")
    if fc_clx + lcb_clx > len(table_stream):
        raise ValueError("Clx extends past the end of the table stream")
    clx = table_stream[fc_clx : fc_clx + lcb_clx]

    runs = _piece_runs(clx)
    total_characters = sum(run[2] for run in runs)
    if total_characters > _MAX_DOC_TEXT_CHARACTERS:
        raise ValueError("DOC text character limit exceeded")

    text = "".join(
        _piece_text(word_document, fc=fc, is_compressed=is_compressed, char_count=char_count)
        for fc, is_compressed, char_count in runs
    )

    segments: list[NormalizedSegment] = []
    paragraph_number = 0
    for raw_paragraph in text.split("\r"):
        value = raw_paragraph.strip("\x00").strip()
        if not value:
            continue
        paragraph_number += 1
        segments.append(NormalizedSegment(locator={"paragraph": paragraph_number}, text=value))

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="doc",
        source_name=source_name,
        segments=segments,
        metadata={
            "processor": "archiv.legacy-office-doc",
            "processor_version": DOC_PROCESSOR_VERSION,
            "piece_count": len(runs),
            "table_stream": "1Table" if which_table else "0Table",
            "macros_executed": False,
        },
    )


# --- .ppt (MS-PPT) ---

_PPT_STREAM_NAME = "PowerPoint Document"
_RT_SLIDE = 0x03EE
_RT_TEXT_CHARS_ATOM = 0x0FA0
_RT_TEXT_BYTES_ATOM = 0x0FA8
_RT_CRYPT_SESSION10_CONTAINER = 0x2F14
_MAX_PPT_STREAM_BYTES = 128 * 1024 * 1024
_MAX_PPT_RECORD_DEPTH = 32
_MAX_PPT_RECORDS = 200_000
_MAX_PPT_TEXT_CHARACTERS = 20_000_000


@dataclass
class _PptWalkState:
    records: int = 0
    slide: int = 0
    shape: int = 0
    total_characters: int = 0
    segments: list[NormalizedSegment] = field(default_factory=lambda: list[NormalizedSegment]())


def _ppt_record_header(data: bytes, offset: int) -> tuple[bool, int, int]:
    if offset + 8 > len(data):
        raise ValueError("PPT record header is truncated")
    ver_instance = struct.unpack_from("<H", data, offset)[0]
    rec_type = struct.unpack_from("<H", data, offset + 2)[0]
    rec_len = struct.unpack_from("<I", data, offset + 4)[0]
    is_container = (ver_instance & 0x000F) == 0x000F
    return is_container, rec_type, rec_len


def _ppt_append_text(state: _PptWalkState, raw_text: str) -> None:
    value = raw_text.strip("\x00").strip()
    if not value:
        return
    state.total_characters += len(value)
    if state.total_characters > _MAX_PPT_TEXT_CHARACTERS:
        raise ValueError("PPT text character limit exceeded")
    state.shape += 1
    slide_number = state.slide if state.slide > 0 else 1
    state.segments.append(
        NormalizedSegment(locator={"slide": slide_number, "shape": state.shape}, text=value)
    )


def _walk_ppt_records(
    data: bytes, start: int, end: int, *, depth: int, state: _PptWalkState
) -> None:
    if depth > _MAX_PPT_RECORD_DEPTH:
        raise ValueError("PPT record nesting depth limit exceeded")
    offset = start
    while offset < end:
        if depth == 0 and offset + 8 > end:
            # The CFB directory's declared stream size need not exactly match the
            # last real record's end (e.g. sector-rounding padding); only the
            # top-level walk tolerates this, since a nested container's boundary is
            # self-declared by its own recLen and should be exact.
            break
        state.records += 1
        if state.records > _MAX_PPT_RECORDS:
            raise ValueError("PPT record count limit exceeded")
        is_container, rec_type, rec_len = _ppt_record_header(data, offset)
        payload_start = offset + 8
        payload_end = payload_start + rec_len
        if payload_end > end:
            raise ValueError("PPT record extends past its container")
        if rec_type == _RT_CRYPT_SESSION10_CONTAINER:
            raise ValueError("encrypted PPT documents are not supported")
        if rec_type == _RT_SLIDE:
            state.slide += 1
            state.shape = 0
            _walk_ppt_records(data, payload_start, payload_end, depth=depth + 1, state=state)
        elif rec_type == _RT_TEXT_CHARS_ATOM:
            text = data[payload_start:payload_end].decode("utf-16-le", errors="strict")
            _ppt_append_text(state, text)
        elif rec_type == _RT_TEXT_BYTES_ATOM:
            text = data[payload_start:payload_end].decode("cp1252", errors="strict")
            _ppt_append_text(state, text)
        elif is_container:
            _walk_ppt_records(data, payload_start, payload_end, depth=depth + 1, state=state)
        offset = payload_end


def normalize_ppt(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Validate and normalize one PowerPoint 97-2003 (binary) presentation without execution."""

    if path.stat().st_size > _MAX_PPT_STREAM_BYTES:
        raise ValueError("PPT package size limit exceeded")

    ole: olefile.OleFileIO[str] = olefile.OleFileIO(str(path))
    try:
        stream = ole.openstream(_PPT_STREAM_NAME).read()
    finally:
        ole.close()

    if len(stream) > _MAX_PPT_STREAM_BYTES:
        raise ValueError("PPT stream size limit exceeded")

    state = _PptWalkState()
    _walk_ppt_records(stream, 0, len(stream), depth=0, state=state)

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="ppt",
        source_name=source_name,
        segments=state.segments,
        metadata={
            "processor": "archiv.legacy-office-ppt",
            "processor_version": PPT_PROCESSOR_VERSION,
            "record_count": state.records,
            # Slide/shape numbers follow record-encounter order rather than resolving
            # SlideListWithText persist IDs; see module docstring.
            "slide_locator_scheme": "encounter-order",
            "macros_executed": False,
        },
    )


__all__ = ["normalize_doc", "normalize_ppt"]
