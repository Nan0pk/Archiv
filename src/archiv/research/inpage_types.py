"""Shared types for bounded native InPage research."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from archiv.research.inpage_cfb_probe import ProbeLimits

ARABIC_RANGES: Final = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
BIDI_CONTROLS: Final = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)
JOINING_CONTROLS: Final = frozenset({0x200C, 0x200D})
NormalizationMode = Literal[
    "raw",
    "exact_nfc",
    "whitespace_normalized",
    "diacritic_insensitive",
    "verse_symbol_normalized",
]


class ExtractionError(ValueError):
    """Expected bounded research failure."""


@dataclass(frozen=True)
class ExtractionLimits:
    """Hard limits for private research extraction."""

    cfb: ProbeLimits = ProbeLimits()
    max_stream_bytes: int = 32 * 1024 * 1024
    max_text_codepoints: int = 2_000_000
    max_records: int = 100_000
    max_record_bytes: int = 16_384
    min_utf16_run_units: int = 6
    max_mapping_bytes: int = 4 * 1024 * 1024
    max_mapping_entries: int = 4096


@dataclass(frozen=True)
class RootStream:
    """One validated root-level native stream."""

    name: str
    variant: str
    stream_size: int
    stream_sha256: str
    sector_size: int
    sector_count: int
    payload: bytes


@dataclass(frozen=True)
class MappingTable:
    """A bounded mapping source with provenance-safe measurements."""

    source_sha256: str
    source_size: int
    mapping_count: int
    duplicates: int
    conflicts: int
    ignored: int
    values: Mapping[int, str]


@dataclass(frozen=True)
class MappingComparison:
    """Sanitized mapping comparison."""

    left_sha256: str
    right_sha256: str
    overlap: int
    agreements: int
    conflicts: int
    left_only: int
    right_only: int
    conflicting_codes: tuple[int, ...]


@dataclass(frozen=True)
class TextMetrics:
    """Sanitized extraction measurements."""

    schema_version: int
    variant: str
    stream_sha256: str
    stream_size: int
    raw_text_sha256: str
    nfc_text_sha256: str
    text_codepoints: int
    arabic_codepoints: int
    ascii_codepoints: int
    replacement_characters: int
    line_count: int
    native_support_claimed: bool
    text_emitted: bool
    details: Mapping[str, int | str]


@dataclass(frozen=True)
class QuranComparison:
    """Sanitized text-comparison result."""

    mode: NormalizationMode
    extracted_sha256: str
    reference_sha256: str
    extracted_length: int
    reference_length: int
    matching_characters: int
    matching_ratio: float
    length_delta: int
    exact_match: bool


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8"))


def is_arabic(codepoint: int) -> bool:
    return any(start <= codepoint <= end for start, end in ARABIC_RANGES)


def basic_text_counts(text: str) -> tuple[int, int, int, int]:
    codepoints = tuple(map(ord, text))
    return (
        len(codepoints),
        sum(is_arabic(codepoint) for codepoint in codepoints),
        sum(0x20 <= codepoint <= 0x7E for codepoint in codepoints),
        text.count("\ufffd"),
    )
