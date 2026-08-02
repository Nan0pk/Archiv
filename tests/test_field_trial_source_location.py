from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))
SPEC = importlib.util.spec_from_file_location(
    "archiv_field_trial_source_location",
    SCRIPT_ROOT / "field_trial" / "source_location.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_public_source_location_probe_is_sanitized(tmp_path: Path) -> None:
    summary: dict[str, object] = {
        "schema_version": "1",
        "benchmark_id": "probe",
        "corpus_document_count": 1,
        "question_count": 1,
        "evidence_limit": 1,
        "aggregate": {
            "retrieval": {
                "questions_with_full_recall": 1,
                "questions_with_retrieval_miss": 0,
                "mean_recall_at_evidence_limit": 1.0,
            },
            "citation_integrity": {
                "structurally_valid_questions": 1,
                "fabricated_identifier_count": 0,
            },
            "answer_quality": {
                "mean_completeness_score": 1.0,
                "unsupported_claim_count": 0,
            },
            "performance": {"median_question_duration_ms": 1.0},
            "dominant_failure": "none",
        },
        "source_navigation": {
            "source_name_available": True,
            "native_locator_available": True,
            "safe_open_command_available": False,
        },
        "defects": [{"category": "source navigation friction"}],
        "questions": [],
        "privacy": {"original_hashes_unchanged": True},
        "report": {"status": "not-run"},
        "known_limitations": [],
    }
    command = f"{sys.executable} -m archiv.cli"
    result = MODULE.apply_source_location_probe(
        summary,
        output=tmp_path / "artifacts",
        archiv_command=command,
    )

    navigation = cast(dict[str, object], result["source_navigation"])
    assert navigation["bounded_source_location_available"] is True
    assert navigation["citation_revalidated"] is True
    assert navigation["original_hash_revalidated"] is True
    assert navigation["read_only"] is True
    assert result["defects"] == []

    rendered = (tmp_path / "artifacts" / "public-results.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in rendered
    assert "public-source-location-probe.txt" not in rendered
    assert "ARCHIV-SOURCE-LOCATION-PROBE-2026" not in rendered
    assert json.loads(rendered)["source_navigation"]["read_only"] is True
