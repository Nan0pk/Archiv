from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from archiv.research.inpage_types import ExtractionError
from archiv.research.quran_reference import (
    EXPECTED_AYAH_COUNT,
    SURAH_AYAH_COUNTS,
    compare_juz,
    comparison_json,
    parse_amrayn_json,
    parse_tanzil_xml,
    verses_for_juz,
)


def _complete_json() -> bytes:
    surahs = []
    for surah, count in enumerate(SURAH_AYAH_COUNTS, start=1):
        surahs.append(
            {
                "id": surah,
                "name": f"surah-{surah}",
                "transliteration": f"surah-{surah}",
                "type": "test",
                "total_verses": count,
                "verses": [
                    {"id": ayah, "text": f"س{surah} آ{ayah}"}
                    for ayah in range(1, count + 1)
                ],
            }
        )
    return json.dumps(surahs, ensure_ascii=False).encode("utf-8")


def _complete_xml() -> bytes:
    root = ET.Element("quran")
    for surah, count in enumerate(SURAH_AYAH_COUNTS, start=1):
        sura = ET.SubElement(root, "sura", index=str(surah), name=f"surah-{surah}")
        for ayah in range(1, count + 1):
            ET.SubElement(
                sura,
                "aya",
                index=str(ayah),
                text=f"س{surah} آ{ayah}",
            )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def test_parse_complete_amrayn_json_and_juz_boundaries() -> None:
    reference = parse_amrayn_json(_complete_json())
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
    parsed = json.loads(_complete_json())
    parsed[66]["total_verses"] = 31
    with pytest.raises(ExtractionError, match="wrong verse count"):
        parse_amrayn_json(json.dumps(parsed).encode())


def test_parse_complete_tanzil_xml_and_reject_entities() -> None:
    reference = parse_tanzil_xml(_complete_xml())
    assert len(reference.verses) == EXPECTED_AYAH_COUNT
    assert reference.verses[0].key == "1:1"
    assert reference.verses[-1].key == "114:6"
    with pytest.raises(ExtractionError, match="entities are forbidden"):
        parse_tanzil_xml(b'<!DOCTYPE quran [<!ENTITY x "boom">]><quran>&x;</quran>')


def test_sequence_comparison_tolerates_extra_non_quran_text() -> None:
    reference = parse_amrayn_json(_complete_json())
    verses = verses_for_juz(reference, 29)
    extracted = "عنوان " + "\n".join(verse.text for verse in verses) + " اختتام"
    comparison = compare_juz(extracted, reference, 29)
    sequence = comparison.verse_sequence["diacritic_insensitive"]
    whole = comparison.whole_text["diacritic_insensitive"]
    assert sequence.matched_verses == 431
    assert sequence.contiguous_prefix_verses == 431
    assert sequence.matching_ratio == 1.0
    assert whole.exact_match is False
    serialized = comparison_json(comparison)
    assert "عنوان" not in serialized
    assert "س67" not in serialized
    assert '"native_support_claimed": false' in serialized


def test_sequence_comparison_reports_first_missing_verse() -> None:
    reference = parse_amrayn_json(_complete_json())
    verses = list(verses_for_juz(reference, 30))
    del verses[10]
    extracted = " ".join(verse.text for verse in verses)
    comparison = compare_juz(extracted, reference, 30)
    sequence = comparison.verse_sequence["whitespace"]
    assert sequence.matched_verses == 563
    assert sequence.contiguous_prefix_verses == 10
    assert sequence.first_unmatched_key == "78:11"
    assert sequence.last_matched_key == "114:6"


def test_rejects_unsupported_juz() -> None:
    reference = parse_amrayn_json(_complete_json())
    with pytest.raises(ExtractionError, match="unsupported research juz"):
        verses_for_juz(reference, 28)
