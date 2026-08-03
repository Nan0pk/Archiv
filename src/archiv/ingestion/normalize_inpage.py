"""Local, bounded native InPage text normalization.

The InPage100 high-byte framing rule and first-key legacy character values are
implemented from the independently inspectable parser guide and mapping data in
ShakesVision/html-experiments at commit
1f9bc57a6cdbe6ad69f18b38913e1af06ba5b41a. Archiv's implementation is an
independent Python implementation; attribution and limitations are documented
in docs/inpage-ingestion.md.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Final

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.research.inpage_container import read_native_root_stream
from archiv.research.inpage_types import BIDI_CONTROLS, JOINING_CONTROLS, RootStream, is_arabic

MAX_SEGMENTS: Final = 20_000
MAX_SEGMENT_CHARS: Final = 4_000
MIN_RUN_CHARS: Final = 4
LEGACY_GAP_BYTES: Final = 4
LATIN_WORD: Final = re.compile(r"[A-Za-z]{2}")

# First-key legacy values used by InPage100. Codes without a stable first-key
# value are intentionally absent and terminate a candidate run rather than
# producing guessed text.
INPAGE100_TO_UNICODE: Final[dict[int, str]] = {
    0x20: " ",
    0x81: "ا",
    0x82: "ب",
    0x83: "پ",
    0x84: "ت",
    0x85: "ٹ",
    0x86: "ث",
    0x87: "ج",
    0x88: "چ",
    0x89: "ح",
    0x8A: "خ",
    0x8B: "د",
    0x8C: "ڈ",
    0x8D: "ذ",
    0x8E: "ر",
    0x8F: "ڑ",
    0x90: "ز",
    0x91: "ژ",
    0x92: "س",
    0x93: "ش",
    0x94: "ص",
    0x95: "ض",
    0x96: "ط",
    0x97: "ظ",
    0x98: "ع",
    0x99: "غ",
    0x9A: "ف",
    0x9B: "ق",
    0x9C: "ک",
    0x9D: "گ",
    0x9E: "ل",
    0x9F: "م",
    0xA0: "ن",
    0xA1: "ں",
    0xA2: "و",
    0xA3: "ئ",
    0xA4: "ی",
    0xA5: "ے",
    0xA6: "ہ",
    0xA7: "ھ",
    0xA8: "ٍ",
    0xA9: " ",
    0xAA: "ِ",
    0xAB: "َ",
    0xAC: "ُ",
    0xAD: "ّ",
    0xAE: "ؑ",
    0xAF: " ",
    0xB0: "ٖ",
    0xB1: "ْ",
    0xB2: " ",
    0xB3: "ٓ",
    0xB4: "ْ",
    0xB5: "ٌ",
    0xB6: "ؤ",
    0xB7: "ء",
    0xB8: "ي",
    0xB9: "ۃ",
    0xBA: " ",
    0xBB: " ",
    0xBC: " ",
    0xBD: "ٰ",
    0xBE: "ٗ",
    0xBF: "ٔ",
    0xC0: " ",
    0xC1: " ",
    0xC2: " ",
    0xC3: " ",
    0xC4: " ",
    0xC5: " ",
    0xC6: " ",
    0xC7: "ً",
    0xC8: "آ",
    0xC9: " ",
    0xCA: "إ",
    0xCB: "ﷲ",
    0xCC: " ",
    0xCD: " ",
    0xCE: ":",
    0xCF: "ؔ",
    0xD0: "۰",
    0xD1: "۱",
    0xD2: "۲",
    0xD3: "۳",
    0xD4: "۴",
    0xD5: "۵",
    0xD6: "۶",
    0xD7: "۷",
    0xD8: "۸",
    0xD9: "۹",
    0xDA: "!",
    0xDD: " ",
    0xDE: "٪",
    0xDF: "/",
    0xE0: "…",
    0xE1: ")",
    0xE2: "(",
    0xE3: " ",
    0xE4: "+",
    0xE5: " ",
    0xE6: "ؓ",
    0xE7: "ؒ",
    0xE8: "٭",
    0xE9: ":",
    0xEA: "؛",
    0xEB: "×",
    0xEC: "=",
    0xED: "،",
    0xEE: "؟",
    0xEF: "÷",
    0xF0: " ",
    0xF1: "؍",
    0xF2: "؎",
    0xF3: "۔",
    0xF4: " ",
    0xF5: "ـ",
    0xF7: "؁",
    0xF8: "ؐ",
    0xF9: " ",
    0xFA: "[",
    0xFB: "]",
    0xFC: ".",
    0xFD: "’",
    0xFE: "‘",
    0xFF: " ",
}


def _allowed_unicode(codepoint: int) -> bool:
    return (
        codepoint in {0x0009, 0x000A, 0x000C, 0x000D, 0x00A0}
        or 0x0020 <= codepoint <= 0x007E
        or is_arabic(codepoint)
        or codepoint in BIDI_CONTROLS
        or codepoint in JOINING_CONTROLS
    )


def _useful_text(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_RUN_CHARS:
        return False
    return (
        any(is_arabic(ord(character)) for character in stripped)
        or LATIN_WORD.search(stripped) is not None
    )


def _clean(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def _chunk(text: str) -> list[str]:
    chunks: list[str] = []
    for raw_line in _clean(text).split("\n"):
        line = raw_line.strip()
        while len(line) > MAX_SEGMENT_CHARS:
            boundary = line.rfind(" ", 0, MAX_SEGMENT_CHARS + 1)
            if boundary < MAX_SEGMENT_CHARS // 2:
                boundary = MAX_SEGMENT_CHARS
            piece = line[:boundary].strip()
            if piece:
                chunks.append(piece)
            line = line[boundary:].strip()
        if line:
            chunks.append(line)
    return chunks


def _segments_from_runs(
    runs: list[tuple[int, str]], *, variant: str
) -> tuple[list[NormalizedSegment], int]:
    segments: list[NormalizedSegment] = []
    seen: set[str] = set()
    duplicates = 0
    for offset, run in runs:
        for part, text in enumerate(_chunk(run), 1):
            if not _useful_text(text):
                continue
            if text in seen:
                duplicates += 1
                continue
            seen.add(text)
            segments.append(
                NormalizedSegment(
                    locator={
                        "stream": f"InPage{variant}",
                        "byte_offset": offset,
                        "part": part,
                    },
                    text=text,
                )
            )
            if len(segments) > MAX_SEGMENTS:
                raise ValueError("InPage segment limit exceeded")
    return segments, duplicates


def _extract_inpage300(stream: RootStream) -> tuple[list[NormalizedSegment], dict[str, object]]:
    payload = stream.payload[: len(stream.payload) - len(stream.payload) % 2]
    runs: list[tuple[int, str]] = []
    current: list[int] = []
    start = 0
    rejected_units = 0

    def flush() -> None:
        nonlocal current
        if current:
            text = "".join(chr(codepoint) for codepoint in current)
            if _useful_text(text):
                runs.append((start, text))
        current = []

    for offset in range(0, len(payload), 2):
        codepoint = payload[offset] | payload[offset + 1] << 8
        if _allowed_unicode(codepoint) and not 0xD800 <= codepoint <= 0xDFFF:
            if not current:
                start = offset
            current.append(codepoint)
        else:
            flush()
            rejected_units += 1
    flush()
    segments, duplicates = _segments_from_runs(runs, variant="300")
    return segments, {
        "extraction_mode": "bounded_utf16le_stream_runs",
        "candidate_runs": len(runs),
        "rejected_code_units": rejected_units,
        "duplicate_segments_dropped": duplicates,
        "unmapped_legacy_codes": 0,
    }


def _extract_inpage100(stream: RootStream) -> tuple[list[NormalizedSegment], dict[str, object]]:
    payload = stream.payload[: len(stream.payload) - len(stream.payload) % 2]
    runs: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    last_offset = -1
    unknown_codes = 0

    def flush() -> None:
        nonlocal current, last_offset
        if current:
            text = "".join(current)
            if _useful_text(text):
                runs.append((start, text))
        current = []
        last_offset = -1

    for offset in range(0, len(payload), 2):
        low = payload[offset]
        high = payload[offset + 1]
        character: str | None = None
        if high == 0x04:
            if 0x20 <= low <= 0x7E:
                character = chr(low)
            else:
                character = INPAGE100_TO_UNICODE.get(low)
                if character is None:
                    unknown_codes += 1
                    flush()
                    continue
        elif high == 0x00 and low in {0x0A, 0x0D}:
            character = "\n"
        if character is None:
            continue
        if current and offset - last_offset > LEGACY_GAP_BYTES:
            flush()
        if not current:
            start = offset
        current.append(character)
        last_offset = offset
    flush()
    segments, duplicates = _segments_from_runs(runs, variant="100")
    return segments, {
        "extraction_mode": "high_byte_04_legacy_text_units",
        "candidate_runs": len(runs),
        "rejected_code_units": 0,
        "duplicate_segments_dropped": duplicates,
        "unmapped_legacy_codes": unknown_codes,
    }


def normalize_inpage(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Extract searchable native text from a bounded InPage100 or InPage300 file."""

    stream = read_native_root_stream(path)
    if stream.variant == "300":
        segments, measurements = _extract_inpage300(stream)
    elif stream.variant == "100":
        segments, measurements = _extract_inpage100(stream)
    else:
        raise ValueError(f"unsupported native InPage stream variant: {stream.variant}")
    if not segments:
        raise ValueError("native InPage document contains no safely extractable searchable text")

    warnings = [
        "Text is extracted locally in content-stream order; page, frame, style "
        "and exact visual layout are not reconstructed."
    ]
    unknown = measurements["unmapped_legacy_codes"]
    if isinstance(unknown, int) and unknown:
        warnings.append(
            f"Skipped {unknown} unmapped legacy text units instead of guessing characters."
        )

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="inp",
        source_name=source_name,
        segments=segments,
        metadata={
            "native_format": "InPage",
            "native_variant": stream.variant,
            "content_stream": stream.name,
            "content_stream_size": stream.stream_size,
            "content_stream_sha256": stream.stream_sha256,
            "text_fidelity": "searchable_best_effort",
            "native_text_extracted": True,
            "layout_supported": False,
            "segment_count": len(segments),
            "warnings": warnings,
            **measurements,
        },
    )
