from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/trial-regressions"


def test_trial_regression_fixture_manifest_is_complete_and_private_safe() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1"
    assert "no private" in manifest["provenance"].lower()
    case_ids = {case["id"] for case in manifest["cases"]}
    assert case_ids == {
        "watermark-scan",
        "duplicate-revision",
        "source-crowding-table",
        "malformed-container",
        "multilingual-rtl",
        "long-document",
        "unsupported-claim",
        "interrupted-ingest",
    }
    for case in manifest["cases"]:
        for filename in case["files"]:
            assert (FIXTURES / filename).is_file()


def test_trial_protocol_and_beta_gates_are_explicit() -> None:
    protocol = (ROOT / "docs/trial-protocol-v1.md").read_text(encoding="utf-8")
    done = (ROOT / "docs/definition-of-done.md").read_text(encoding="utf-8")
    for required in (
        "Small",
        "Medium",
        "Large",
        "malformed",
        "superseded",
        "scanned PDFs",
        "Required-source recall",
        "Interrupted-run recovery",
    ):
        assert required in protocol or required in done
    assert "NO-GO" in done
    assert "Citation correctness" in done
