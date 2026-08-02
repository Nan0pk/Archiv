from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from field_trial.common import load_benchmark  # noqa: E402
from field_trial.fixtures import _source_maps, generate_public_corpus  # noqa: E402

from archiv.cli import app  # noqa: E402
from archiv.contracts import RetrievalDiagnostics, RetrievalQueryVariant, RetrievalSelection  # noqa: E402
from archiv.ingestion import ingest_file  # noqa: E402
from archiv.search import (  # noqa: E402
    derive_query_variants,
    rebuild_search_index,
    retrieve_evidence,
    sanitized_retrieval_diagnostics,
    search_documents,
)

BENCHMARK = ROOT / "benchmarks/field_trial/benchmark.json"
runner = CliRunner()


def _field_trial_home(tmp_path: Path) -> tuple[dict[str, object], Path, dict[str, str]]:
    benchmark = load_benchmark(BENCHMARK)
    corpus = tmp_path / "corpus"
    home = tmp_path / "home"
    generate_public_corpus(benchmark, corpus)
    for path in sorted(corpus.iterdir()):
        ingest_file(path, home=home)
    rebuild_search_index(home=home)
    _, by_filename = _source_maps(benchmark)
    return benchmark, home, by_filename


def test_query_derivation_is_stable_bounded_and_offline() -> None:
    objective = "What dates or deadlines are established?"
    first = derive_query_variants(objective)
    second = derive_query_variants(objective)
    assert first == second
    variants, terms, concepts = first
    assert 1 <= len(variants) <= 24
    assert variants[0].query == objective
    assert variants[0].kind == "exact-objective"
    assert "dates-and-deadlines" in concepts
    assert "dates" in terms or "deadlines" in terms


def test_all_public_benchmark_questions_retrieve_every_expected_source(tmp_path: Path) -> None:
    benchmark, home, by_filename = _field_trial_home(tmp_path)
    limit = int(benchmark["evidence_limit"])
    failures: list[dict[str, object]] = []
    for question in cast(Sequence[Mapping[str, object]], benchmark["questions"]):
        package = retrieve_evidence(str(question["question"]), home=home, evidence_limit=limit)
        retrieved = {
            by_filename.get(result.citation.source_name, "UNKNOWN")
            for result in package.results
        }
        expected = set(cast(Sequence[str], question["expected_sources"]))
        missing = sorted(expected - retrieved)
        if missing:
            failures.append(
                {
                    "question_id": question["id"],
                    "question": question["question"],
                    "expected": sorted(expected),
                    "retrieved": sorted(retrieved),
                    "missing": missing,
                    "diagnostics": package.diagnostics.model_dump(mode="json"),
                }
            )
    assert not failures, json.dumps(failures, indent=2, sort_keys=True)


def test_literal_search_semantics_remain_unchanged(tmp_path: Path) -> None:
    _, home, by_filename = _field_trial_home(tmp_path)
    question = "What work is described as complete?"
    assert search_documents(question, home=home, limit=20) == []
    natural = retrieve_evidence(question, home=home, evidence_limit=8)
    source_ids = {
        by_filename.get(result.citation.source_name, "UNKNOWN") for result in natural.results
    }
    assert "STATUS" in source_ids


def test_retrieval_results_and_diagnostics_repeat_exactly(tmp_path: Path) -> None:
    _, home, _ = _field_trial_home(tmp_path)
    objective = "What remains unfinished?"
    first = retrieve_evidence(objective, home=home, evidence_limit=8)
    second = retrieve_evidence(objective, home=home, evidence_limit=8)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_sanitized_diagnostics_remove_private_identifiers() -> None:
    private_objective = "Review /home/private-user/SecretPlan.docx for Project Falcon"
    private_segment = "a" * 64
    private_object = "b" * 64
    diagnostics = RetrievalDiagnostics(
        original_objective=private_objective,
        derived_terms=["secretplan", "falcon"],
        query_variants=[
            RetrievalQueryVariant(
                kind="derived-term",
                query="SecretPlan.docx",
                weight=60,
                result_count=1,
            )
        ],
        evidence_limit=8,
        candidate_count=1,
        selected_count=1,
        selections=[
            RetrievalSelection(
                segment_id=private_segment,
                object_sha256=private_object,
                source_name="SecretPlan.docx",
                locator={"paragraph": 7},
                rank=-1.0,
                score=61.0,
                matched_queries=["SecretPlan.docx"],
            )
        ],
    )
    rendered = json.dumps(sanitized_retrieval_diagnostics(diagnostics), sort_keys=True)
    for forbidden in (
        private_objective,
        "/home/private-user",
        "SecretPlan.docx",
        "Project Falcon",
        "secretplan",
        "falcon",
        private_segment,
        private_object,
        "paragraph",
    ):
        assert forbidden not in rendered


def test_ask_and_report_expose_retrieval_explanation_option() -> None:
    ask_help = runner.invoke(app, ["ask", "--help"])
    report_help = runner.invoke(app, ["report", "--help"])
    assert ask_help.exit_code == 0, ask_help.output
    assert report_help.exit_code == 0, report_help.output
    assert "--explain-retrieval" in ask_help.output
    assert "--explain-retrieval" in report_help.output
