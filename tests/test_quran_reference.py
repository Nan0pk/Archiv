# pyright: reportPrivateUsage=false

from __future__ import annotations

import functools
import json
import xml.etree.ElementTree as ET
from typing import cast

import pytest

from archiv.research.inpage_types import ExtractionError
from archiv.research.quran_reference import (
    DIAGNOSTIC_MODES,
    EXPECTED_AYAH_COUNT,
    PRIMARY_MODES,
    SURAH_AYAH_COUNTS,
    QuranReference,
    _bounded_text_comparison,
    compare_juz,
    comparison_json,
    parse_amrayn_json,
    parse_tanzil_xml,
    verses_for_juz,
)


@functools.lru_cache(maxsize=1)
def _complete_json() -> bytes:
    surahs: list[dict[str, object]] = []
    for surah, count in enumerate(SURAH_AYAH_COUNTS, start=1):
        verses: list[dict[str, object]] = [
            {"id": ayah, "text": f"سورة{surah} آية{ayah}"} for ayah in range(1, count + 1)
        ]
        surahs.append(
            {
                "id": surah,
                "name": f"surah-{surah}",
                "transliteration": f"surah-{surah}",
                "type": "test",
                "total_verses": count,
                "verses": verses,
            }
        )
    return json.dumps(surahs, ensure_ascii=False).encode("utf-8")


@functools.lru_cache(maxsize=1)
def _complete_xml() -> bytes:
    root = ET.Element("quran")
    for surah, count in enumerate(SURAH_AYAH_COUNTS, start=1):
        sura = ET.SubElement(root, "sura", index=str(surah), name=f"surah-{surah}")
        for ayah in range(1, count + 1):
            ET.SubElement(
                sura,
                "aya",
                index=str(ayah),
                text=f"سورة{surah} آية{ayah}",
            )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


@functools.lru_cache(maxsize=1)
def _reference() -> QuranReference:
    return parse_amrayn_json(_complete_json())


def test_parse_complete_amrayn_json_and_juz_boundaries() -> None:
    reference = _reference()
    assert len(reference.verses) == EXPECTED_AYAH_COUNT
    juz_29 = verses_for_juz(reference, 29)
    juz_30 = verses_for_juz(reference, 30)
    assert juz_29[0].key == "67:1"
    assert juz_29[-1].key == "77:50"
    assert len(juz_29) == 431
    assert juz_30[0].key == "78:1"
    assert juz_30[-1].key == "114:6"
    assert len(juz_30) == 564


def test_amrayn_json_rejects_declared_count_mismatch() -> None:
    parsed_value: object = json.loads(_complete_json())
    assert isinstance(parsed_value, list)
    parsed = cast(list[object], parsed_value)
    surah = parsed[66]
    assert isinstance(surah, dict)
    cast(dict[str, object], surah)["total_verses"] = 31
    with pytest.raises(ExtractionError, match="wrong verse count"):
        parse_amrayn_json(json.dumps(parsed).encode())


def test_parse_complete_tanzil_xml_and_reject_entities_anywhere() -> None:
    reference = parse_tanzil_xml(_complete_xml())
    assert len(reference.verses) == EXPECTED_AYAH_COUNT
    assert reference.verses[0].key == "1:1"
    assert reference.verses[-1].key == "114:6"

    padded = b" " * 9000 + b'<!DOCTYPE quran [<!ENTITY x "boom">]><quran>&x;</quran>'
    with pytest.raises(ExtractionError, match="entities are forbidden"):
        parse_tanzil_xml(padded)


def test_primary_and_diagnostic_normalizations_are_separate() -> None:
    assert PRIMARY_MODES == (
        "exact_nfc",
        "whitespace_normalized",
        "diacritic_insensitive",
        "verse_symbol_normalized",
    )
    assert "arabic_letters_only" not in PRIMARY_MODES
    assert "arabic_letters_only" in DIAGNOSTIC_MODES


def test_bounded_edit_counts_and_character_class_differences() -> None:
    comparison = _bounded_text_comparison("ا١،ب", "ا٢.ج!", "exact_nfc")
    assert comparison.comparison_limited is False
    assert comparison.edit_counts_kind == "minimal_levenshtein"
    assert comparison.insertions + comparison.deletions + comparison.substitutions == (
        comparison.edit_distance
    )
    assert comparison.numeral_differences == 2
    assert comparison.punctuation_differences >= 1
    assert comparison.exact_match is False


def test_large_comparison_fails_over_to_labelled_nonminimal_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("archiv.research.quran_reference.MAX_EDIT_CELLS", 1)
    comparison = _bounded_text_comparison("abc", "axc", "exact_nfc")
    assert comparison.comparison_limited is True
    assert comparison.edit_counts_kind == "nonminimal_position_aligned"
    assert comparison.substitutions == 1


def test_sequence_comparison_reports_extra_prefix_and_suffix() -> None:
    reference = _reference()
    verses = verses_for_juz(reference, 29)
    extracted = "عنوان " + "\n".join(verse.text for verse in verses) + " اختتام"
    comparison = compare_juz(extracted, reference, 29)
    sequence = comparison.verse_sequence["whitespace_normalized"]
    whole = comparison.whole_text["whitespace_normalized"]
    assert sequence.matched_verses == 431
    assert sequence.contiguous_prefix_verses == 431
    assert sequence.complete_in_order_coverage is True
    assert sequence.unmatched_prefix_characters is not None
    assert sequence.unmatched_prefix_characters > 0
    assert sequence.unmatched_suffix_characters is not None
    assert sequence.unmatched_suffix_characters > 0
    assert whole.exact_match is False
    serialized = comparison_json(comparison)
    assert "عنوان" not in serialized
    assert "سورة67" not in serialized
    assert '"native_support_claimed": false' in serialized


def test_sequence_comparison_reports_first_missing_verse() -> None:
    reference = _reference()
    verses = list(verses_for_juz(reference, 30))
    del verses[10]
    extracted = " ".join(verse.text for verse in verses)
    comparison = compare_juz(extracted, reference, 30)
    sequence = comparison.verse_sequence["whitespace_normalized"]
    assert sequence.matched_verses == 563
    assert sequence.contiguous_prefix_verses == 10
    assert sequence.first_unmatched_key == "78:11"
    assert sequence.last_matched_key == "114:6"
    assert sequence.complete_in_order_coverage is False


def test_reversal_sorting_and_wrong_juz_cannot_create_complete_coverage() -> None:
    reference = _reference()
    verses_29 = list(verses_for_juz(reference, 29))
    verses_30 = list(verses_for_juz(reference, 30))

    reversed_comparison = compare_juz(
        " ".join(verse.text for verse in reversed(verses_29)),
        reference,
        29,
    )
    sorted_comparison = compare_juz(
        " ".join(verse.text for verse in sorted(verses_29, key=lambda verse: verse.key)),
        reference,
        29,
    )
    wrong_juz_comparison = compare_juz(
        " ".join(verse.text for verse in verses_30),
        reference,
        29,
    )

    assert (
        reversed_comparison.verse_sequence["whitespace_normalized"].complete_in_order_coverage
        is False
    )
    assert (
        sorted_comparison.verse_sequence["whitespace_normalized"].complete_in_order_coverage
        is False
    )
    assert (
        wrong_juz_comparison.verse_sequence["whitespace_normalized"].complete_in_order_coverage
        is False
    )


def test_arabic_letters_only_is_diagnostic_not_success_evidence() -> None:
    reference = _reference()
    verses = verses_for_juz(reference, 29)
    extracted = " ".join(verse.text for verse in verses)
    comparison = compare_juz(extracted, reference, 29)
    diagnostic = comparison.verse_sequence["arabic_letters_only"]
    assert diagnostic.success_eligible is False
    assert comparison.whole_text["arabic_letters_only"].success_eligible is False


def test_raw_mode_is_distinct_from_exact_nfc() -> None:
    reference = _reference()
    verses = verses_for_juz(reference, 29)
    extracted = "\r\n".join(verse.text for verse in verses)
    comparison = compare_juz(extracted, reference, 29)
    assert comparison.whole_text["raw"].exact_match is False
    assert comparison.whole_text["exact_nfc"].exact_match is True


def test_rejects_unsupported_juz() -> None:
    with pytest.raises(ExtractionError, match="unsupported research juz"):
        verses_for_juz(_reference(), 28)
