"""Legacy RTF (Rich Text Format) normalization.

RTF's body is 7-bit ASCII control words and literal text; non-ASCII content
is always escaped (``\\'hh`` hex or ``\\uN`` Unicode), so this is a bounded
plain-text extractor, not a binary parser. It walks control words to find
paragraph text and explicitly discards non-body destinations (font/colour/
style tables, document info, and — critically — embedded ``\\pict`` and
``\\object``/``\\objdata`` payloads, which are never decoded or activated;
their declared byte length is skipped as opaque data only). RTF has no
native encryption concept, so there is nothing to fail closed on there.

This does not attempt full RTF fidelity (see ``known_limits`` in the
compatibility matrix): footnotes, headers/footers, field codes, and index/TOC
entries are treated as non-body and dropped, and a ``\\uN`` replacement run
that itself contains a control word is not fully honored per spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from archiv.contracts import NormalizedDocument, NormalizedSegment

RTF_PROCESSOR_VERSION = "1"

MAX_RTF_BYTES = 64 * 1024 * 1024
MAX_GROUP_DEPTH = 200
MAX_TOKENS = 2_000_000
MAX_TEXT_CHARACTERS = 20_000_000
DEFAULT_CODEPAGE = "cp1252"

# Destinations whose content is never document body text; their whole group
# (including any embedded binary payload) is discarded rather than emitted.
_SKIP_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "listtable",
    "listoverridetable",
    "revtbl",
    "rsidtbl",
    "generator",
    "info",
    "pict",
    "object",
    "objdata",
    "objclass",
    "objname",
    "fldinst",
    "footnote",
    "header",
    "footer",
    "headerf",
    "footerf",
    "headerl",
    "footerl",
    "headerr",
    "footerr",
    "xe",
    "tc",
    "bkmkstart",
    "bkmkend",
    "atnid",
    "atnauthor",
    "atndate",
    "annotation",
    "atrfstart",
    "atrfend",
    "themedata",
    "colorschememapping",
    "latentstyles",
    "datastore",
    "shppict",
    "nonshppict",
}

_CODEPAGE_MAP = {
    "1252": "cp1252",
    "1250": "cp1250",
    "1251": "cp1251",
    "1253": "cp1253",
    "1254": "cp1254",
    "1255": "cp1255",
    "1256": "cp1256",
    "1257": "cp1257",
    "1258": "cp1258",
    "874": "cp874",
    "932": "cp932",
    "936": "cp936",
    "949": "cp949",
    "950": "cp950",
    "10000": "mac_roman",
}


class _Frame:
    __slots__ = ("skip", "unicode_skip")

    def __init__(self, skip: bool, unicode_skip: int) -> None:
        self.skip = skip
        self.unicode_skip = unicode_skip


@dataclass
class _RtfState:
    paragraphs: list[str] = field(default_factory=lambda: [""])
    codepage: str = DEFAULT_CODEPAGE
    pending_skip_units: int = 0
    total_characters: int = 0
    tokens: int = 0


def _count_token(state: _RtfState) -> None:
    state.tokens += 1
    if state.tokens > MAX_TOKENS:
        raise ValueError("RTF token count limit exceeded")


def _append_text(state: _RtfState, frame: _Frame, text: str) -> None:
    if frame.skip or not text:
        return
    state.total_characters += len(text)
    if state.total_characters > MAX_TEXT_CHARACTERS:
        raise ValueError("RTF text character limit exceeded")
    state.paragraphs[-1] += text


def _new_paragraph(state: _RtfState, frame: _Frame) -> None:
    if frame.skip:
        return
    state.paragraphs.append("")


def _apply_control_symbol(symbol: str, state: _RtfState, stack: list[_Frame]) -> None:
    frame = stack[-1]
    if symbol == "~":
        _append_text(state, frame, " ")
    elif symbol in ("-", "_"):
        _append_text(state, frame, "-")
    elif symbol == "*":
        frame.skip = True
    # Other control symbols (\:, \|, \\ page-number markers, etc.) carry no
    # extractable text and are silently ignored.


def _apply_control_word(name: str, param: int | None, state: _RtfState, stack: list[_Frame]) -> int:
    """Apply one control word's effect; return extra raw bytes to skip (for \\bin)."""

    frame = stack[-1]
    if name == "par":
        _new_paragraph(state, frame)
        return 0
    if name == "line":
        _append_text(state, frame, "\n")
        return 0
    if name == "tab":
        _append_text(state, frame, "\t")
        return 0
    if name == "u":
        if param is None:
            raise ValueError("RTF \\u control word is missing its numeric parameter")
        code_point = param if param >= 0 else param + 0x10000
        if 0 <= code_point <= 0x10FFFF:
            _append_text(state, frame, chr(code_point))
        state.pending_skip_units = frame.unicode_skip
        return 0
    if name == "uc":
        if param is not None and param >= 0:
            frame.unicode_skip = param
        return 0
    if name == "ansicpg":
        if param is not None:
            state.codepage = _CODEPAGE_MAP.get(str(param), DEFAULT_CODEPAGE)
        return 0
    if name == "bin":
        if param is None or param < 0:
            raise ValueError("RTF \\bin control word has an invalid byte count")
        return param
    if name in _SKIP_DESTINATIONS:
        frame.skip = True
        return 0
    return 0


def _handle_control(data: bytes, index: int, state: _RtfState, stack: list[_Frame]) -> int:
    length = len(data)
    if index >= length:
        raise ValueError("RTF control sequence is truncated at end of file")
    first = data[index]

    if first in (0x5C, 0x7B, 0x7D):  # \\  \{  \}
        _append_text(state, stack[-1], chr(first))
        return index + 1

    if first == 0x27:  # \'hh
        if index + 3 > length:
            raise ValueError("RTF hex escape is truncated")
        hex_digits = data[index + 1 : index + 3]
        try:
            value = int(hex_digits, 16)
        except ValueError as error:
            raise ValueError("RTF hex escape is not valid hexadecimal") from error
        if state.pending_skip_units > 0:
            state.pending_skip_units -= 1
        else:
            try:
                decoded = bytes([value]).decode(state.codepage, errors="replace")
            except LookupError:
                decoded = bytes([value]).decode("cp1252", errors="replace")
            _append_text(state, stack[-1], decoded)
        return index + 3

    if (0x61 <= first <= 0x7A) or (0x41 <= first <= 0x5A):
        start = index
        while index < length and ((0x61 <= data[index] <= 0x7A) or (0x41 <= data[index] <= 0x5A)):
            index += 1
        name = data[start:index].decode("ascii")
        param: int | None = None
        if index < length and (data[index] == 0x2D or 0x30 <= data[index] <= 0x39):
            param_start = index
            if data[index] == 0x2D:
                index += 1
            while index < length and 0x30 <= data[index] <= 0x39:
                index += 1
            param = int(data[param_start:index])
        if index < length and data[index] == 0x20:
            index += 1  # the single delimiting space is part of the control word
        skip_bytes = _apply_control_word(name, param, state, stack)
        if skip_bytes:
            if index + skip_bytes > length:
                raise ValueError("RTF \\bin binary run extends past the end of the file")
            index += skip_bytes
        return index

    _apply_control_symbol(chr(first) if first < 0x80 else "?", state, stack)
    return index + 1


def normalize_rtf(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Validate and normalize one RTF document without execution."""

    if path.stat().st_size > MAX_RTF_BYTES:
        raise ValueError("RTF size limit exceeded")
    data = path.read_bytes()
    if not data.startswith(b"{\\rtf1"):
        raise ValueError("RTF file does not start with the {\\rtf1 magic")

    state = _RtfState()
    stack: list[_Frame] = [_Frame(skip=False, unicode_skip=1)]
    index = 0
    length = len(data)

    while index < length:
        _count_token(state)
        byte = data[index]
        if byte == 0x7B:  # {
            if len(stack) >= MAX_GROUP_DEPTH:
                raise ValueError("RTF group nesting depth limit exceeded")
            stack.append(_Frame(skip=stack[-1].skip, unicode_skip=stack[-1].unicode_skip))
            state.pending_skip_units = 0
            index += 1
            continue
        if byte == 0x7D:  # }
            if len(stack) <= 1:
                raise ValueError("RTF has an unmatched closing brace")
            stack.pop()
            state.pending_skip_units = 0
            index += 1
            continue
        if byte == 0x5C:  # backslash
            index = _handle_control(data, index + 1, state, stack)
            continue
        if byte in (0x0D, 0x0A):
            index += 1
            continue
        if state.pending_skip_units > 0:
            state.pending_skip_units -= 1
            index += 1
            continue
        char = chr(byte) if byte < 0x80 else data[index : index + 1].decode("latin-1")
        _append_text(state, stack[-1], char)
        index += 1

    if len(stack) != 1:
        raise ValueError("RTF has an unmatched opening brace")

    segments: list[NormalizedSegment] = []
    paragraph_number = 0
    for raw in state.paragraphs:
        value = raw.strip()
        if not value:
            continue
        paragraph_number += 1
        segments.append(NormalizedSegment(locator={"paragraph": paragraph_number}, text=value))

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="rtf",
        source_name=source_name,
        segments=segments,
        metadata={
            "processor": "archiv.legacy-rtf",
            "processor_version": RTF_PROCESSOR_VERSION,
            "codepage": state.codepage,
            "macros_executed": False,
            "embedded_objects_opened": False,
        },
    )


__all__ = ["normalize_rtf"]
