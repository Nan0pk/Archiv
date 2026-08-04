from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from PIL import Image
from typer import Typer
from typer.testing import CliRunner

import archiv.ocr_benchmark as benchmark
from archiv.ocr_benchmark_cli import register_ocr_benchmark_command


def _fixture(tmp_path: Path, *, private: bool = False) -> benchmark.FixtureRecord:
    image = tmp_path / "fixture.png"
    Image.new("RGB", (20, 20), "white").save(image)
    return benchmark.FixtureRecord(
        fixture_id="private-secret" if private else "public-fixture",
        language="Urdu",
        ground_truth="ثبوت ۱۲۳۔",
        expected_lines=("ثبوت ۱۲۳۔",),
        image_path=image,
        image_sha256=benchmark.sha256_file(image),
        tags=("urdu", "clean"),
        source_kind="private_inpage_render" if private else "archiv-authored-synthetic",
        private=private,
    )


def _execution(
    fixture: benchmark.FixtureRecord,
    *,
    text: str = "ثبوت ۱۲۳۔",
    status: benchmark.CandidateStatus = "succeeded",
    warning: str | None = None,
) -> benchmark.CandidateExecution:
    outputs = (
        {
            fixture.fixture_id: benchmark.EngineText(
                text=text,
                lines=benchmark.normalize_lines(text),
                elapsed_seconds=0.1,
                peak_rss_kib=1234,
                coordinates=[[0, 0, 10, 10]],
                confidence=[0.9],
            )
        }
        if status == "succeeded"
        else {}
    )
    return benchmark.CandidateExecution(
        status=status,
        engine="fake",
        configuration="test",
        engine_evidence={"version": "1", "license": "test"},
        model_evidence={"sha256": "abc", "bytes": 3},
        outputs=outputs,
        warning=warning,
    )


def test_score_text_preserves_legacy_metrics_and_reading_order() -> None:
    exact = benchmark.score_text(
        "ثبوت ۱۲۳۔",
        "ثبوت ۱۲۳۔",
        ["ثبوت ۱۲۳۔"],
        ["ثبوت ۱۲۳۔"],
    )
    assert exact["cer"] == 0.0
    assert exact["wer"] == 0.0
    assert exact["punctuation_error_rate"] == 0.0
    assert exact["numeral_error_rate"] == 0.0
    assert exact["reading_order_error_rate"] == 0.0

    changed = benchmark.score_text("abc", "adcX")
    assert changed["cer"] == 0.666667
    assert changed["character_edits"] == {
        "substitutions": 1,
        "deletions": 0,
        "insertions": 1,
    }
    assert changed["hallucinated_characters"] == 1


def test_default_candidates_include_contributed_model_only_when_installed() -> None:
    assert benchmark.default_candidates(["ara", "eng", "osd", "urd"]) == [
        "eng",
        "ara",
        "urd",
        "eng+ara+urd",
    ]
    assert benchmark.default_candidates(["ara", "eng", "urd_naw"]) == [
        "eng",
        "ara",
        "urd_naw",
        "eng+ara+urd_naw",
    ]


def test_private_manifest_requires_lawful_evidence_and_blocks_path_escape(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    manifest = {
        "schema_version": "1",
        "fixtures": [
            {
                "fixture_id": "bad",
                "language": "Urdu",
                "ground_truth": "secret",
                "expected_lines": ["secret"],
                "image_path": "../outside.png",
                "source_kind": "private_inpage_render",
                "lawful_basis": "operator owns the document",
                "generation_method": "local InPage export",
            }
        ],
    }
    (private / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(benchmark.OcrBenchmarkError, match="remain inside"):
        benchmark._fixture_record_from_entry(  # pyright: ignore[reportPrivateUsage]
            cast(dict[str, object], manifest["fixtures"][0]),
            private,
            True,
        )


def test_candidate_scoring_records_model_evidence_and_missing_results(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    succeeded = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:good",
        _execution(fixture),
        [fixture],
    )
    assert succeeded["status"] == "succeeded"
    assert succeeded["model_evidence"] == {"sha256": "abc", "bytes": 3}
    assert cast(dict[str, object], succeeded["aggregate"])["cer"] == 0.0

    missing = benchmark.CandidateExecution(
        "succeeded",
        "fake",
        "missing",
        {},
        {},
        {},
        None,
    )
    failed = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:missing",
        missing,
        [fixture],
    )
    assert failed["status"] == "failed"
    assert "omitted fixture results" in str(failed["warning"])


def test_ranking_retains_failed_unavailable_and_blocked_candidates(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    good = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:good",
        _execution(fixture),
        [fixture],
    )
    bad = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:bad",
        _execution(fixture, text="غلط"),
        [fixture],
    )
    unavailable = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:none",
        _execution(fixture, status="unavailable", warning="not installed"),
        [fixture],
    )
    blocked = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:blocked",
        _execution(fixture, status="blocked", warning="licence unclear"),
        [fixture],
    )
    ranking = benchmark.rank_candidates([bad, unavailable, good, blocked])
    assert [item["candidate_id"] for item in ranking] == [
        "fake:bad",
        "fake:none",
        "fake:good",
        "fake:blocked",
    ]
    assert next(item for item in ranking if item["candidate_id"] == "fake:good")["rank"] == 1
    assert next(item for item in ranking if item["candidate_id"] == "fake:none")["rank"] is None


def test_sanitized_summary_excludes_private_text_paths_and_fixture_ids(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, private=True)
    result = benchmark._score_execution(  # pyright: ignore[reportPrivateUsage]
        "fake:good",
        _execution(fixture),
        [fixture],
    )
    report = {
        "scope": "test",
        "environment": {"platform": "test"},
        "public_fixture_count": 0,
        "private_fixture_count": 1,
        "corpus_manifest_sha256": "manifest",
        "candidate_results": [result],
        "ranking": benchmark.rank_candidates([result]),
        "route_evidence": benchmark.route_evidence([result]),
        "target_hardware_status": "local",
    }
    sanitized = benchmark.sanitized_summary(report)
    serialized = json.dumps(sanitized, sort_keys=True, ensure_ascii=False)
    assert "ثبوت" not in serialized
    assert "private-secret" not in serialized
    assert str(tmp_path) not in serialized
    assert sanitized["corpus"] == {
        "public_fixture_count": 0,
        "private_fixture_count": 1,
        "private_content_included": False,
        "corpus_manifest_sha256": "manifest",
    }


def test_run_benchmark_writes_deterministic_shareable_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    def fake_corpus(
        output_dir: Path,
        private_corpus: Path | None = None,
    ) -> dict[str, object]:
        del output_dir, private_corpus
        return {"records": [fixture], "manifest_sha256": "manifest"}

    def runner(
        fixtures: list[benchmark.FixtureRecord] | tuple[benchmark.FixtureRecord, ...],
        output_dir: Path,
    ) -> benchmark.CandidateExecution:
        del output_dir
        return _execution(fixtures[0])

    monkeypatch.setattr(benchmark, "build_corpus", fake_corpus)
    output_one = tmp_path / "one"
    output_two = tmp_path / "two"
    report_one = benchmark.run_benchmark(
        output_one,
        ["eng"],
        ["tesseract"],
        runner_overrides={"tesseract:eng": runner},
    )
    report_two = benchmark.run_benchmark(
        output_two,
        ["eng"],
        ["tesseract"],
        runner_overrides={"tesseract:eng": runner},
    )
    assert (output_one / "shareable-summary.json").read_text() == (
        output_two / "shareable-summary.json"
    ).read_text()
    assert report_one["recommended_candidate"] == "tesseract:eng"
    assert report_one["product_policy"] == {
        "normal_ingestion_configuration_changed": False,
        "ocr_remains_derived_evidence": True,
        "native_text_overwritten": False,
        "automatic_indexing_threshold": None,
    }
    assert Path(str(report_two["summary_path"])).is_file()


def test_cli_keeps_candidates_option_and_adds_engine_and_private_corpus_options() -> None:
    app = Typer()
    register_ocr_benchmark_command(app)
    result = CliRunner().invoke(app, ["benchmark-ocr", "--help"])
    assert result.exit_code == 0
    assert "--candidates" in result.stdout
    assert "--engines" in result.stdout
    assert "--private-corpus" in result.stdout
