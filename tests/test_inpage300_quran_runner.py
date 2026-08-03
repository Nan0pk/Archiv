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
