from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from PIL import Image

import archiv.ocr_benchmark as benchmark


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_score_text_reports_exact_and_inserted_text() -> None:
    exact = benchmark.score_text("ثبوت ۱۲۳۔", "ثبوت ۱۲۳۔")
    assert exact["cer"] == 0.0
    assert exact["wer"] == 0.0
    assert exact["punctuation_error_rate"] == 0.0
    assert exact["numeral_error_rate"] == 0.0

    changed = benchmark.score_text("abc", "adcX")
    assert changed["cer"] == 0.666667
    assert changed["wer"] == 1.0
    assert changed["character_edits"] == {
        "substitutions": 1,
        "deletions": 0,
        "insertions": 1,
    }
    assert changed["hallucinated_characters"] == 1


def test_default_candidates_require_installed_language_models() -> None:
    assert benchmark._default_candidates(["ara", "eng", "osd", "urd"]) == [
        "eng",
        "ara",
        "urd",
        "eng+ara+urd",
    ]
    assert benchmark._default_candidates(["ara", "eng", "urd_naw"]) == [
        "eng",
        "ara",
        "urd_naw",
        "eng+ara+urd_naw",
    ]


def test_run_benchmark_writes_scored_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "tesseract",
        f"""#!{sys.executable}
import sys

if "--version" in sys.argv:
    print("tesseract 5.5.0-test")
elif "--list-langs" in sys.argv:
    print("List of available languages (1):")
    print("eng")
else:
    print("Archiv verifies local evidence 2026.")
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    def fake_corpus(output_dir: Path) -> dict[str, object]:
        image_path = output_dir / "fixtures" / "english-clean.png"
        image_path.parent.mkdir(parents=True)
        Image.new("RGB", (32, 32), "white").save(image_path)
        return {
            "fixtures": [
                {
                    "fixture_id": "english-clean",
                    "language": "English",
                    "text": "Archiv verifies local evidence 2026.",
                    "image_path": "fixtures/english-clean.png",
                    "image_sha256": "fixture-sha",
                }
            ],
            "manifest_sha256": "manifest-sha",
        }

    monkeypatch.setattr(benchmark, "build_corpus", fake_corpus)
    report = benchmark.run_benchmark(tmp_path / "benchmark", ["eng"])

    assert report["recommended_candidate"] == "eng"
    assert report["engine_version"] == "tesseract 5.5.0-test"
    aggregates_value = report["aggregates"]
    assert isinstance(aggregates_value, list)
    aggregates = cast(list[dict[str, object]], aggregates_value)
    assert aggregates[0]["cer"] == 0.0
    report_path = Path(str(report["report_path"]))
    assert report_path.is_file()
    persisted_value = json.loads(report_path.read_text(encoding="utf-8"))
    assert isinstance(persisted_value, dict)
    persisted = cast(dict[str, object], persisted_value)
    runs_value = persisted["runs"]
    assert isinstance(runs_value, list)
    runs = cast(list[dict[str, object]], runs_value)
    metrics_value = runs[0]["metrics"]
    assert isinstance(metrics_value, dict)
    metrics = cast(dict[str, object], metrics_value)
    assert metrics["wer"] == 0.0
