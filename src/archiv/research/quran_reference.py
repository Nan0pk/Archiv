"""Bounded Quran reference parsing and sequence comparison for InPage research."""

from __future__ import annotations

import json
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import cast

from archiv.research.inpage_types import ExtractionError, NormalizationMode, sha256
from archiv.research.inpage_validation import normalize_quran_text

MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_NORMALIZED_CODEPOINTS = 2_000_000
MAX_EDIT_CELLS = 2_000_000
MAX_EDIT_AXIS = 100_000
MAX_XML_ELEMENTS = 7_000
MAX_SEQUENCE_SEARCH_WORK = 100_000_000
EXPECTED_SURAH_COUNT = 114
EXPECTED_AYAH_COUNT = 6236

PRIMARY_MODES: tuple[NormalizationMode, ...] = (
    "exact_nfc",
    "whitespace_normalized",
    "diacritic_insensitive",
    "verse_symbol_normalized",
)
DIAGNOSTIC_MODES: tuple[NormalizationMode, ...] = (
    "raw",
    "arabic_letters_only",
)
ALL_MODES = PRIMARY_MODES + DIAGNOSTIC_MODES

SURAH_AYAH_COUNTS = (
    7,
    286,
    200,
    176,
    120,
    165,
    206,
    75,
    129,
    109,
    123,
    111,
    43,
    52,
    99,
    128,
    111,
    110,
    98,
    135,
    112,
    78,
    118,
    64,
    77,
    227,
    93,
    88,
    69,
    60,
    34,
    30,
    73,
    54,
    45,
    83,
    182,
    88,
    75,
    85,
    54,
    53,
    89,
    59,
    37,
    35,
    38,
    29,
    18,
    45,
    60,
    49,
    62,
    55,
    78,
    96,
    29,
    22,
    24,
    13,
    14,
    11,
    11,
    18,
    12,
    12,
    30,
    52,
    52,
    44,
    28,
    28,
    20,
    56,
    40,
    31,
    50,
    40,
    46,
    42,
    29,
    19,
    36,
    25,
    22,
    17,
    19,
    26,
    30,
    20,
    15,
    21,
    11,
    8,
    8,
    19,
    5,
    8,
    8,
    11,
    11,
    8,
    3,
    9,
    5,
    4,
    7,
    3,
    6,
    3,
    5,
    4,
    5,
    6,
)

JUZ_SURAH_RANGES: Mapping[int, tuple[int, int]] = {
    29: (67, 77),
    30: (78, 114),
}


@dataclass(frozen=True)
class QuranVerse:
    surah: int
    ayah: int
    text: str

    @property
    def key(self) -> str:
        return f"{self.surah}:{self.ayah}"


@dataclass(frozen=True)
class QuranReference:
    source_name: str
    source_sha256: str
    source_size: int
    verses: tuple[QuranVerse, ...]


@dataclass(frozen=True)
class BoundedTextComparison:
    mode: NormalizationMode
    success_eligible: bool
    algorithm: str
    edit_counts_kind: str
    comparison_limited: bool
    extracted_sha256: str
    reference_sha256: str
    extracted_length: int
    reference_length: int
    matching_characters: int
    matching_ratio: float
    common_prefix_characters: int
    common_suffix_characters: int
    insertions: int
    deletions: int
    substitutions: int
    edit_distance: int
    punctuation_differences: int
    numeral_differences: int
    length_delta: int
    exact_match: bool


@dataclass(frozen=True)
class VerseSequenceMetrics:
    mode: NormalizationMode
    success_eligible: bool
    expected_verses: int
    matched_verses: int
    verse_boundary_agreement: int
    contiguous_prefix_verses: int
    matching_ratio: float
    first_matching_key: str | None
    first_unmatched_key: str | None
    last_matched_key: str | None
    ordering_monotonic: bool
    complete_in_order_coverage: bool
    unmatched_prefix_characters: int | None
    unmatched_suffix_characters: int | None
    search_work: int
    extracted_normalized_length: int
    reference_normalized_length: int
    extracted_normalized_sha256: str
    reference_normalized_sha256: str
    text_emitted: bool = False
    native_support_claimed: bool = False


@dataclass(frozen=True)
class JuzComparison:
    source_name: str
    source_sha256: str
    juz: int
    first_verse: str
    last_verse: str
    expected_verses: int
    primary_modes: tuple[NormalizationMode, ...]
    diagnostic_modes: tuple[NormalizationMode, ...]
    whole_text: Mapping[str, BoundedTextComparison]
    verse_sequence: Mapping[str, VerseSequenceMetrics]
    text_emitted: bool = False
    native_support_claimed: bool = False


def _checked_bytes(data: bytes, *, label: str) -> bytes:
    if not data:
        raise ExtractionError(f"{label} is empty")
    if len(data) > MAX_REFERENCE_BYTES:
        raise ExtractionError(f"{label} exceeds the reference size limit")
    return data


def _checked_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtractionError(f"{label} must contain non-empty text")
    if "\x00" in value:
        raise ExtractionError(f"{label} contains a NUL character")
    return value


def _checked_normalized(text: str, mode: NormalizationMode) -> str:
    normalized = normalize_quran_text(text, mode)
    if len(normalized) > MAX_NORMALIZED_CODEPOINTS:
        raise ExtractionError("normalized comparison text exceeds the codepoint limit")
    return normalized


def _validate_complete_quran(verses: Sequence[QuranVerse]) -> tuple[QuranVerse, ...]:
    if len(verses) != EXPECTED_AYAH_COUNT:
        raise ExtractionError(f"expected {EXPECTED_AYAH_COUNT} ayahs, found {len(verses)}")
    expected_position = 0
    for surah, ayah_count in enumerate(SURAH_AYAH_COUNTS, start=1):
        for ayah in range(1, ayah_count + 1):
            verse = verses[expected_position]
            if (verse.surah, verse.ayah) != (surah, ayah):
                raise ExtractionError(f"expected verse {surah}:{ayah}, found {verse.key}")
            expected_position += 1
    return tuple(verses)


def parse_amrayn_json(data: bytes) -> QuranReference:
    """Parse the pinned amrayn/quran-text JSON without trusting its declarations."""

    raw = _checked_bytes(data, label="Quran JSON")
    try:
        parsed_value: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExtractionError("Quran JSON is not valid bounded UTF-8 JSON") from error
    if not isinstance(parsed_value, list):
        raise ExtractionError("Quran JSON must contain a surah list")
    parsed = cast(list[object], parsed_value)
    if len(parsed) != EXPECTED_SURAH_COUNT:
        raise ExtractionError(f"Quran JSON must contain {EXPECTED_SURAH_COUNT} surahs")

    verses: list[QuranVerse] = []
    for expected_surah, raw_surah in enumerate(parsed, start=1):
        if not isinstance(raw_surah, dict):
            raise ExtractionError("Quran JSON surah entry must be an object")
        surah = cast(dict[str, object], raw_surah)
        if surah.get("id") != expected_surah:
            raise ExtractionError(f"expected surah {expected_surah}")
        expected_count = SURAH_AYAH_COUNTS[expected_surah - 1]
        if surah.get("total_verses") != expected_count:
            raise ExtractionError(f"surah {expected_surah} declares the wrong verse count")
        raw_verses_value = surah.get("verses")
        if not isinstance(raw_verses_value, list):
            raise ExtractionError(f"surah {expected_surah} has no verse list")
        raw_verses = cast(list[object], raw_verses_value)
        if len(raw_verses) != expected_count:
            raise ExtractionError(f"surah {expected_surah} has the wrong verse list")
        for expected_ayah, raw_verse in enumerate(raw_verses, start=1):
            if not isinstance(raw_verse, dict):
                raise ExtractionError("Quran JSON verse entry must be an object")
            verse = cast(dict[str, object], raw_verse)
            if verse.get("id") != expected_ayah:
                raise ExtractionError(f"expected verse {expected_surah}:{expected_ayah}")
            text = _checked_text(
                verse.get("text"),
                label=f"verse {expected_surah}:{expected_ayah}",
            )
            verses.append(QuranVerse(expected_surah, expected_ayah, text))

    return QuranReference(
        source_name="amrayn/quran-text quran-full-tashkeel.json",
        source_sha256=sha256(raw),
        source_size=len(raw),
        verses=_validate_complete_quran(verses),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def parse_tanzil_xml(data: bytes) -> QuranReference:
    """Parse a downloaded Tanzil Quran XML file without changing its text."""

    raw = _checked_bytes(data, label="Tanzil Quran XML")
    upper_raw = raw.upper()
    if b"<!DOCTYPE" in upper_raw or b"<!ENTITY" in upper_raw:
        raise ExtractionError("Tanzil Quran XML declarations and entities are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ExtractionError("Tanzil Quran XML is malformed") from error
    if sum(1 for _ in root.iter()) > MAX_XML_ELEMENTS:
        raise ExtractionError("Tanzil Quran XML exceeds the element limit")
    if _local_name(root.tag) != "quran":
        raise ExtractionError("Tanzil Quran XML has the wrong root element")

    sura_elements = [child for child in root if _local_name(child.tag) == "sura"]
    if len(sura_elements) != EXPECTED_SURAH_COUNT:
        raise ExtractionError(f"Tanzil XML must contain {EXPECTED_SURAH_COUNT} surahs")

    verses: list[QuranVerse] = []
    for expected_surah, sura in enumerate(sura_elements, start=1):
        if sura.attrib.get("index") != str(expected_surah):
            raise ExtractionError(f"expected Tanzil surah {expected_surah}")
        aya_elements = [child for child in sura if _local_name(child.tag) == "aya"]
        expected_count = SURAH_AYAH_COUNTS[expected_surah - 1]
        if len(aya_elements) != expected_count:
            raise ExtractionError(f"Tanzil surah {expected_surah} has the wrong verse count")
        for expected_ayah, aya in enumerate(aya_elements, start=1):
            if aya.attrib.get("index") != str(expected_ayah):
                raise ExtractionError(f"expected Tanzil verse {expected_surah}:{expected_ayah}")
            text = _checked_text(
                aya.attrib.get("text"),
                label=f"Tanzil verse {expected_surah}:{expected_ayah}",
            )
            verses.append(QuranVerse(expected_surah, expected_ayah, text))

    return QuranReference(
        source_name="Tanzil Quran XML",
        source_sha256=sha256(raw),
        source_size=len(raw),
        verses=_validate_complete_quran(verses),
    )


def verses_for_juz(reference: QuranReference, juz: int) -> tuple[QuranVerse, ...]:
    try:
        first_surah, last_surah = JUZ_SURAH_RANGES[juz]
    except KeyError as error:
        raise ExtractionError(f"unsupported research juz: {juz}") from error
    selected = tuple(
        verse for verse in reference.verses if first_surah <= verse.surah <= last_surah
    )
    if not selected:
        raise ExtractionError(f"reference contains no verses for juz {juz}")
    expected_count = sum(SURAH_AYAH_COUNTS[first_surah - 1 : last_surah])
    if len(selected) != expected_count:
        raise ExtractionError(f"reference has an incomplete juz {juz}")
    return selected


def _operation_key(value: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    cost, insertions, deletions, substitutions = value
    return cost, substitutions, deletions, insertions


def _exact_edit_counts(left: str, right: str) -> tuple[int, int, int, int]:
    """Return exact Levenshtein cost and operation counts with bounded linear memory."""

    previous = [(index, index, 0, 0) for index in range(len(right) + 1)]
    for left_index, left_character in enumerate(left, start=1):
        current: list[tuple[int, int, int, int]] = [(left_index, 0, left_index, 0)]
        for right_index, right_character in enumerate(right, start=1):
            if left_character == right_character:
                diagonal = previous[right_index - 1]
            else:
                cost, insertions, deletions, substitutions = previous[right_index - 1]
                diagonal = (cost + 1, insertions, deletions, substitutions + 1)
            cost, insertions, deletions, substitutions = previous[right_index]
            deletion = (cost + 1, insertions, deletions + 1, substitutions)
            cost, insertions, deletions, substitutions = current[right_index - 1]
            insertion = (cost + 1, insertions + 1, deletions, substitutions)
            current.append(min((diagonal, deletion, insertion), key=_operation_key))
        previous = current
    return previous[-1]


def _bounded_edit_counts(
    left: str,
    right: str,
) -> tuple[str, str, bool, int, int, int, int]:
    cells = (len(left) + 1) * (len(right) + 1)
    if (
        cells <= MAX_EDIT_CELLS
        and len(left) <= MAX_EDIT_AXIS
        and len(right) <= MAX_EDIT_AXIS
    ):
        cost, insertions, deletions, substitutions = _exact_edit_counts(left, right)
        return (
            "bounded_linear_memory_levenshtein_v1",
            "minimal_levenshtein",
            False,
            insertions,
            deletions,
            substitutions,
            cost,
        )

    overlap = min(len(left), len(right))
    substitutions = sum(
        left_character != right_character
        for left_character, right_character in zip(left, right, strict=False)
    )
    deletions = max(len(left) - overlap, 0)
    insertions = max(len(right) - overlap, 0)
    return (
        "position_aligned_fallback_v1",
        "nonminimal_position_aligned",
        True,
        insertions,
        deletions,
        substitutions,
        insertions + deletions + substitutions,
    )


def _is_category_character(character: str, category: str) -> bool:
    if category == "punctuation":
        return unicodedata.category(character).startswith("P")
    if category == "numeral":
        return character.isdigit()
    raise AssertionError(f"unsupported category: {category}")


def _category_difference(left: str, right: str, *, category: str) -> int:
    left_counts: Counter[str] = Counter(
        character for character in left if _is_category_character(character, category)
    )
    right_counts: Counter[str] = Counter(
        character for character in right if _is_category_character(character, category)
    )
    return sum(abs(left_counts[key] - right_counts[key]) for key in left_counts | right_counts)


def _bounded_text_comparison(
    extracted_text: str,
    reference_text: str,
    mode: NormalizationMode,
) -> BoundedTextComparison:
    left = _checked_normalized(extracted_text, mode)
    right = _checked_normalized(reference_text, mode)
    matching = sum(
        left_character == right_character
        for left_character, right_character in zip(left, right, strict=False)
    )
    denominator = max(len(left), len(right))
    prefix = 0
    for left_character, right_character in zip(left, right, strict=False):
        if left_character != right_character:
            break
        prefix += 1
    suffix = 0
    suffix_limit = min(len(left), len(right)) - prefix
    while suffix < suffix_limit and left[-(suffix + 1)] == right[-(suffix + 1)]:
        suffix += 1
    (
        algorithm,
        edit_counts_kind,
        comparison_limited,
        insertions,
        deletions,
        substitutions,
        edit_distance,
    ) = _bounded_edit_counts(left, right)
    return BoundedTextComparison(
        mode=mode,
        success_eligible=mode in PRIMARY_MODES,
        algorithm=algorithm,
        edit_counts_kind=edit_counts_kind,
        comparison_limited=comparison_limited,
        extracted_sha256=sha256(left.encode("utf-8")),
        reference_sha256=sha256(right.encode("utf-8")),
        extracted_length=len(left),
        reference_length=len(right),
        matching_characters=matching,
        matching_ratio=1.0 if denominator == 0 else matching / denominator,
        common_prefix_characters=prefix,
        common_suffix_characters=suffix,
        insertions=insertions,
        deletions=deletions,
        substitutions=substitutions,
        edit_distance=edit_distance,
        punctuation_differences=_category_difference(left, right, category="punctuation"),
        numeral_differences=_category_difference(left, right, category="numeral"),
        length_delta=len(left) - len(right),
        exact_match=left == right,
    )


def _sequence_metrics(
    extracted_text: str,
    verses: Sequence[QuranVerse],
    mode: NormalizationMode,
) -> VerseSequenceMetrics:
    normalized_extracted = _checked_normalized(extracted_text, mode)
    normalized_verses = [_checked_normalized(verse.text, mode) for verse in verses]
    normalized_reference = " ".join(normalized_verses)
    cursor = 0
    matched = 0
    prefix = 0
    prefix_open = True
    first_matching: str | None = None
    first_unmatched: str | None = None
    last_matching: str | None = None
    first_position: int | None = None
    last_end: int | None = None
    search_work = 0
    for verse, normalized_verse in zip(verses, normalized_verses, strict=True):
        if not normalized_verse:
            raise ExtractionError(f"verse {verse.key} is empty after normalization")
        search_work += len(normalized_extracted) - cursor
        if search_work > MAX_SEQUENCE_SEARCH_WORK:
            raise ExtractionError("verse sequence comparison exceeds the search-work limit")
        position = normalized_extracted.find(normalized_verse, cursor)
        if position < 0:
            if first_unmatched is None:
                first_unmatched = verse.key
            prefix_open = False
            continue
        matched += 1
        if first_matching is None:
            first_matching = verse.key
            first_position = position
        last_matching = verse.key
        last_end = position + len(normalized_verse)
        if prefix_open:
            prefix += 1
        cursor = last_end
    complete = matched == len(verses)
    return VerseSequenceMetrics(
        mode=mode,
        success_eligible=mode in PRIMARY_MODES,
        expected_verses=len(verses),
        matched_verses=matched,
        verse_boundary_agreement=matched,
        contiguous_prefix_verses=prefix,
        matching_ratio=matched / len(verses),
        first_matching_key=first_matching,
        first_unmatched_key=first_unmatched,
        last_matched_key=last_matching,
        ordering_monotonic=True,
        complete_in_order_coverage=complete,
        unmatched_prefix_characters=first_position,
        unmatched_suffix_characters=(
            None if last_end is None else len(normalized_extracted) - last_end
        ),
        search_work=search_work,
        extracted_normalized_length=len(normalized_extracted),
        reference_normalized_length=len(normalized_reference),
        extracted_normalized_sha256=sha256(normalized_extracted.encode("utf-8")),
        reference_normalized_sha256=sha256(normalized_reference.encode("utf-8")),
    )


def compare_juz(
    extracted_text: str,
    reference: QuranReference,
    juz: int,
) -> JuzComparison:
    """Compare private extracted text with a complete reference without emitting text."""

    verses = verses_for_juz(reference, juz)
    reference_text = "\n".join(verse.text for verse in verses)
    whole_text = {
        mode: _bounded_text_comparison(extracted_text, reference_text, mode)
        for mode in ALL_MODES
    }
    verse_sequence = {
        mode: _sequence_metrics(extracted_text, verses, mode) for mode in ALL_MODES
    }
    return JuzComparison(
        source_name=reference.source_name,
        source_sha256=reference.source_sha256,
        juz=juz,
        first_verse=verses[0].key,
        last_verse=verses[-1].key,
        expected_verses=len(verses),
        primary_modes=PRIMARY_MODES,
        diagnostic_modes=DIAGNOSTIC_MODES,
        whole_text=whole_text,
        verse_sequence=verse_sequence,
    )


def comparison_json(comparison: JuzComparison) -> str:
    return json.dumps(asdict(comparison), ensure_ascii=True, sort_keys=True)
