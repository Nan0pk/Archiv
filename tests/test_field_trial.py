from __future__ import annotations

import importlib.util
import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_field_trial.py"
BENCHMARK = ROOT / "benchmarks/field_trial/benchmark.json"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("archiv_field_trial", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
FIELD_TRIAL: Any = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FIELD_TRIAL
SPEC.loader.exec_module(FIELD_TRIAL)


def test_benchmark_definition_is_valid_and_broad() -> None:
    benchmark = FIELD_TRIAL.load_benchmark(BENCHMARK)
    questions = benchmark["questions"]
    corpus = benchmark["corpus"]
    assert isinstance(questions, list)
    assert isinstance(corpus, list)
    assert len(questions) >= 20
    assert {item["format"] for item in corpus} >= {
        "text",
        "markdown",
        "pdf",
        "docx",
        "xlsx",
        "pptx",
    }
    categories = {item["category"] for item in questions}
    assert "missing evidence" in categories
    assert "contradictory evidence" in categories
    assert "current-versus-superseded version selection" in categories
    assert "question requiring several citations" in categories


def test_public_corpus_generation_is_deterministic(tmp_path: Path) -> None:
    benchmark = FIELD_TRIAL.load_benchmark(BENCHMARK)
    first = FIELD_TRIAL.generate_public_corpus(benchmark, tmp_path / "first")
    second = FIELD_TRIAL.generate_public_corpus(benchmark, tmp_path / "second")
    assert first == second
    assert len(first) == len(benchmark["corpus"])


def test_malformed_benchmark_rejects_unknown_source(tmp_path: Path) -> None:
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    payload["questions"][0]["expected_sources"] = ["DOES-NOT-EXIST"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FIELD_TRIAL.BenchmarkError, match="unknown sources"):
        FIELD_TRIAL.load_benchmark(path)


def test_malformed_benchmark_rejects_too_few_questions(tmp_path: Path) -> None:
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    payload["questions"] = payload["questions"][:2]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FIELD_TRIAL.BenchmarkError, match="at least 20"):
        FIELD_TRIAL.load_benchmark(path)


def test_private_mode_requires_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--local-only"):
        FIELD_TRIAL.validate_private_request(tmp_path, False)
    FIELD_TRIAL.validate_private_request(tmp_path, True)


def test_private_path_filename_and_excerpt_redaction(tmp_path: Path) -> None:
    private_path = str(tmp_path / "Secret Project" / "private-plan.docx")
    payload = {
        "path": private_path,
        "filename": "private-plan.docx",
        "question": "What is the private decision?",
        "excerpt": "Confidential answer",
        "nested": {"message": f"Read {private_path}"},
    }
    redacted = FIELD_TRIAL.redact_private(payload, [private_path, "private-plan.docx"])
    rendered = json.dumps(redacted)
    assert private_path not in rendered
    assert "private-plan.docx" not in rendered
    assert "Confidential answer" not in rendered
    assert "What is the private decision?" not in rendered


def test_structural_citation_validation_accepts_retrieved_ids() -> None:
    response = {
        "paragraphs": [{"text": "Fact", "citation_ids": ["CIT-1"]}],
        "claims": [{"statement": "Claim", "citation_ids": ["CIT-2"]}],
    }
    result = FIELD_TRIAL.validate_structural_citations(response, {"CIT-1", "CIT-2"})
    assert result["valid"] is True
    assert result["errors"] == []


def test_structural_citation_validation_rejects_malformed_and_unknown_ids() -> None:
    response = {
        "paragraphs": [{"text": "Fact", "citation_ids": ["SOURCE-1", "CIT-9"]}],
        "claims": [],
    }
    result = FIELD_TRIAL.validate_structural_citations(response, {"CIT-1"})
    assert result["valid"] is False
    assert len(result["errors"]) == 2


def test_missing_evidence_scoring_rewards_honesty() -> None:
    question = {
        "required_facts": [],
        "forbidden_claims": ["rotated every 90 days"],
        "acceptable_insufficient_evidence": True,
        "expected_contradiction": False,
    }
    response = {
        "paragraphs": [],
        "claims": [],
        "insufficient_evidence": ["The policy is not established."],
        "contradictions": [],
    }
    score = FIELD_TRIAL.score_answer(question, response)
    assert score["honesty_ok"] is True
    assert score["completeness_score"] == 1.0
    assert score["unsupported_claims"] == []


def test_contradiction_scoring_requires_acknowledgement() -> None:
    question = {
        "required_facts": [],
        "forbidden_claims": [],
        "acceptable_insufficient_evidence": False,
        "expected_contradiction": True,
    }
    missing = FIELD_TRIAL.score_answer(
        question,
        {"paragraphs": [], "claims": [], "insufficient_evidence": [], "contradictions": []},
    )
    present = FIELD_TRIAL.score_answer(
        question,
        {
            "paragraphs": [],
            "claims": [],
            "insufficient_evidence": [],
            "contradictions": ["Sources conflict."],
        },
    )
    assert missing["contradiction_ok"] is False
    assert present["contradiction_ok"] is True


def test_retrieval_recall_calculation() -> None:
    metrics = FIELD_TRIAL.calculate_retrieval_metrics(
        ["A", "B"], ["B", "X", "A"], evidence_limit=3
    )
    assert metrics["recall_at_evidence_limit"] == 1.0
    assert metrics["expected_source_ranks"] == {"A": 3, "B": 1}
    assert metrics["irrelevant_source_count"] == 1
    assert metrics["required_displaced"] is False


def test_fake_model_server_returns_valid_fixture_response() -> None:
    benchmark = FIELD_TRIAL.load_benchmark(BENCHMARK)
    prompt = """USER QUESTION / OBJECTIVE:
Pilot start date

ALLOWED CITATION IDENTIFIERS:
CIT-1

EVIDENCE PACKAGE:
[CIT-1] Source: trial-schedule.pdf (page 1)
Excerpt: Pilot start date: August 5, 2026.
"""
    with FIELD_TRIAL.FakeModelServer(benchmark) as server:
        request = Request(
            server.endpoint + "/v1/chat/completions",
            data=json.dumps(
                {"model": "fixture", "messages": [{"role": "user", "content": prompt}]}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = json.loads(urlopen(request, timeout=5).read().decode())  # noqa: S310
    content = json.loads(body["choices"][0]["message"]["content"])
    assert content["paragraphs"][0]["citation_ids"] == ["CIT-1"]
    assert "August 5, 2026" in content["paragraphs"][0]["text"]


def test_fake_model_failure_mode_is_detectable() -> None:
    benchmark = FIELD_TRIAL.load_benchmark(BENCHMARK)
    with FIELD_TRIAL.FakeModelServer(benchmark, mode="error") as server:
        request = Request(
            server.endpoint + "/v1/chat/completions",
            data=json.dumps(
                {"model": "fixture", "messages": [{"role": "user", "content": "test"}]}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError):
            urlopen(request, timeout=5)  # noqa: S310


def test_safe_artifact_scan_detects_home_paths(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text(
        json.dumps({"path": "/home/private-user/project/file.txt"}), encoding="utf-8"
    )
    errors = FIELD_TRIAL.scan_safe_artifacts(tmp_path)
    assert errors


def test_public_report_markdown_contains_measured_fields() -> None:
    summary: dict[str, Any] = {
        "corpus_document_count": 12,
        "question_count": 22,
        "aggregate": {
            "retrieval": {
                "questions_with_full_recall": 10,
                "questions_with_retrieval_miss": 12,
                "mean_recall_at_evidence_limit": 0.5,
            },
            "citation_integrity": {
                "structurally_valid_questions": 22,
                "fabricated_identifier_count": 0,
            },
            "answer_quality": {
                "mean_completeness_score": 0.5,
                "unsupported_claim_count": 0,
            },
            "performance": {"median_question_duration_ms": 20.0},
            "dominant_failure": "query construction failure",
        },
        "defects": [
            {
                "category": "query construction failure",
                "observed": "Observed",
                "expected": "Expected",
                "frequency": 12,
                "severity": "high",
                "user_impact": "Impact",
                "smallest_corrective_layer": "query layer",
            }
        ],
    }
    markdown = FIELD_TRIAL._markdown(summary)
    assert "Retrieval misses: 12" in markdown
    assert "Dominant failure: query construction failure" in markdown


def test_public_output_schema_stability(tmp_path: Path) -> None:
    benchmark = FIELD_TRIAL.load_benchmark(BENCHMARK)
    manifest = FIELD_TRIAL.generate_public_corpus(benchmark, tmp_path / "corpus")
    assert set(manifest[0]) == {"source_id", "filename", "format", "sha256", "size_bytes"}
    assert benchmark["schema_version"] == "1"


def test_private_copy_preserves_source_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "private.md"
    original.write_text("private fixture", encoding="utf-8")
    copied, hashes = FIELD_TRIAL._copy_private_corpus(source, tmp_path / "copy")
    assert len(copied) == 1
    assert FIELD_TRIAL.sha256_file(original) == hashes[str(original)]
    assert original.read_text(encoding="utf-8") == "private fixture"


def test_public_benchmark_executes_end_to_end(tmp_path: Path) -> None:
    args = Namespace(
        benchmark=BENCHMARK,
        output=tmp_path / "artifacts",
        archiv_command="archiv",
        render_report=False,
    )
    summary = FIELD_TRIAL.run_public_trial(args)
    aggregate = summary["aggregate"]
    assert summary["question_count"] >= 20
    assert summary["privacy"]["original_hashes_unchanged"] is True
    assert aggregate["citation_integrity"]["structural_failures"] == 0
    assert (args.output / "public-results.json").is_file()
    assert (args.output / "public-report.md").is_file()
