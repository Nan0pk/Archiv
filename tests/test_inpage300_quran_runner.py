from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from archiv.research.inpage_types import ExtractionError

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_inpage300_quran_validation.py"
SPEC = importlib.util.spec_from_file_location("run_inpage300_quran_validation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_verified_git_file_rejects_wrong_blob(tmp_path: Path) -> None:
    path = tmp_path / "source.bin"
    path.write_bytes(b"source")
    with pytest.raises(ExtractionError, match="Git blob mismatch"):
        MODULE._verified_git_file(path, "0" * 40)


def test_fixture_constants_cover_only_juz_29_and_30() -> None:
    assert [fixture["juz"] for fixture in MODULE.FIXTURES] == [29, 30]
    assert [fixture["path"] for fixture in MODULE.FIXTURES] == [
        "inpage/juz_29.inp",
        "inpage/juz_30.inp",
    ]
    assert len(MODULE.TANZIL_SHA256) == 64


def test_sanitized_gate_allows_false_privacy_flags() -> None:
    MODULE._assert_sanitized(
        {
            "privacy": {
                "reference_text_uploaded": False,
                "decoded_text_printed_or_uploaded": False,
            }
        }
    )


def test_sanitized_gate_rejects_exact_content_key() -> None:
    with pytest.raises(ExtractionError, match="forbidden key: decoded_text"):
        MODULE._assert_sanitized({"nested": [{"decoded_text": "secret"}]})


def test_sanitized_gate_rejects_native_text_and_bytes() -> None:
    with pytest.raises(ExtractionError, match="non-ASCII text"):
        MODULE._assert_sanitized({"value": "قرآن"})
    with pytest.raises(ExtractionError, match="raw bytes"):
        MODULE._assert_sanitized({"value": b"fixture"})


def test_automated_gate_ignores_diagnostic_modes() -> None:
    fixture = {
        "juz": 29,
        "comparisons": {
            "reference": {
                "verse_sequence": {
                    mode: {
                        "complete_in_order_coverage": mode == "arabic_letters_only"
                    }
                    for mode in (*MODULE.PRIMARY_MODES, *MODULE.DIAGNOSTIC_MODES)
                }
            }
        },
    }
    gate = MODULE._automated_gate([fixture])
    assert gate["all_fixture_reference_pairs_have_complete_primary_mode"] is False
    assert gate["decision"] == "automated_sequence_gate_not_satisfied"
