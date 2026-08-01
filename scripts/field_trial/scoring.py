"""Command execution, deterministic scoring, aggregation, and safe reporting."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast


def run_command(command: Sequence[str]) -> dict[str, object]:
    started = time.monotonic()
    result = subprocess.run(list(command), text=True, capture_output=True, check=False)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
    }


def _json_output(result: Mapping[str, object]) -> dict[str, object]:
    try:
        value = json.loads(str(result["stdout"]))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"command returned invalid JSON: {result['stderr']}") from error
    if not isinstance(value, dict):
        raise RuntimeError("command JSON was not an object")
    return cast(dict[str, object], value)


def calculate_retrieval_metrics(
    expected_sources: Sequence[str], retrieved_sources: Sequence[str], evidence_limit: int
) -> dict[str, object]:
    expected = list(dict.fromkeys(expected_sources))
    retrieved = list(retrieved_sources)
    found = [item for item in expected if item in retrieved]
    recall = 1.0 if not expected else len(found) / len(expected)
    return {
        "required_source_count": len(expected),
        "retrieved_source_count": len(retrieved),
        "required_sources_found": len(found),
        "recall_at_evidence_limit": round(recall, 4),
        "expected_source_ranks": {
            item: retrieved.index(item) + 1 if item in retrieved else None for item in expected
        },
        "irrelevant_source_count": sum(item not in expected for item in retrieved),
        "required_displaced": bool(
            expected and len(found) < len(expected) and len(retrieved) >= evidence_limit
        ),
    }


def validate_structural_citations(
    response: Mapping[str, object] | None, allowed_ids: set[str]
) -> dict[str, object]:
    errors: list[str] = []
    used: list[str] = []
    if response is None:
        return {"valid": True, "used_ids": used, "errors": errors}
    for group_name in ("paragraphs", "claims"):
        group = response.get(group_name, [])
        if not isinstance(group, list):
            errors.append(f"{group_name} is not a list")
            continue
        for item in group:
            if not isinstance(item, dict):
                errors.append(f"{group_name} contains non-object")
                continue
            citation_ids = item.get("citation_ids")
            if not isinstance(citation_ids, list) or not citation_ids:
                errors.append(f"{group_name} item has no citation_ids")
                continue
            for raw in citation_ids:
                citation_id = str(raw)
                used.append(citation_id)
                if not re.fullmatch(r"CIT-[1-9][0-9]*", citation_id):
                    errors.append(f"malformed citation identifier: {citation_id}")
                elif citation_id not in allowed_ids:
                    errors.append(f"citation outside retrieval package: {citation_id}")
    return {"valid": not errors, "used_ids": used, "errors": errors}


def _answer_text(response: Mapping[str, object] | None) -> str:
    if response is None:
        return ""
    parts: list[str] = []
    for item in cast(Sequence[Mapping[str, object]], response.get("paragraphs", [])):
        parts.append(str(item.get("text", "")))
    for item in cast(Sequence[Mapping[str, object]], response.get("claims", [])):
        parts.append(str(item.get("statement", "")))
    for key in ("insufficient_evidence", "contradictions"):
        parts.extend(str(item) for item in cast(Sequence[object], response.get(key, [])))
    return "\n".join(parts)


def score_answer(
    question: Mapping[str, object], response: Mapping[str, object] | None
) -> dict[str, object]:
    text = _answer_text(response).casefold()
    facts: list[dict[str, object]] = []
    for fact in cast(Sequence[Mapping[str, object]], question["required_facts"]):
        terms = [str(item) for item in cast(Sequence[object], fact["terms"])]
        facts.append(
            {"fact_id": fact.get("id"), "covered": all(term.casefold() in text for term in terms)}
        )
    forbidden = [
        item
        for item in cast(Sequence[str], question.get("forbidden_claims", []))
        if item.casefold() in text
    ]
    insufficient = (
        cast(Sequence[object], response.get("insufficient_evidence", [])) if response else []
    )
    contradictions = cast(Sequence[object], response.get("contradictions", [])) if response else []
    covered = sum(bool(item["covered"]) for item in facts)
    completeness = 1.0 if not facts else covered / len(facts)
    return {
        "required_facts": facts,
        "required_fact_count": len(facts),
        "covered_fact_count": covered,
        "important_evidence_missed": len(facts) - covered,
        "unsupported_claims": forbidden,
        "insufficient_evidence_acknowledged": bool(insufficient),
        "honesty_ok": (
            bool(insufficient)
            if question["acceptable_insufficient_evidence"]
            else not (facts and not covered and bool(insufficient))
        ),
        "contradiction_acknowledged": bool(contradictions),
        "contradiction_ok": bool(contradictions) if question["expected_contradiction"] else True,
        "completeness_score": round(max(0.0, completeness - (0.25 if forbidden else 0)), 4),
    }


def _normalized_text(home: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in (home / "derived").glob("*/normalized/document.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        result[str(document["source_name"])] = "\n".join(
            str(item.get("text", "")) for item in document.get("segments", [])
        )
    return result


def _facts_exist(
    question: Mapping[str, object], normalized: Mapping[str, str], source_files: Mapping[str, str]
) -> bool:
    text = "\n".join(
        normalized.get(source_files[source_id], "")
        for source_id in cast(Sequence[str], question["expected_sources"])
        if source_id in source_files
    ).casefold()
    return all(
        str(term).casefold() in text
        for fact in cast(Sequence[Mapping[str, object]], question["required_facts"])
        for term in cast(Sequence[object], fact["terms"])
    )


def _failure_category(
    retrieval: Mapping[str, object], citations: Mapping[str, object], answer: Mapping[str, object]
) -> str | None:
    if float(retrieval["recall_at_evidence_limit"]) < 1:
        return "query construction failure"
    if not citations["valid"]:
        return "citation validation failure"
    if answer["unsupported_claims"]:
        return "unsupported claim"
    if not answer["contradiction_ok"]:
        return "contradiction handling failure"
    if not answer["honesty_ok"] or float(answer["completeness_score"]) < 1:
        return "model synthesis failure"
    return None


def _copy_report_artifacts(
    payload: Mapping[str, object], home: Path, output: Path
) -> list[str]:
    del payload
    copied: list[str] = []
    for suffix in (".docx", ".pdf"):
        candidate = next(home.rglob(f"*{suffix}"), None)
        if candidate:
            destination = output / f"public-sample-report{suffix}"
            shutil.copy2(candidate, destination)
            copied.append(destination.name)
    pdf = output / "public-sample-report.pdf"
    if pdf.is_file() and shutil.which("pdftoppm"):
        subprocess.run(
            ["pdftoppm", "-png", "-r", "120", str(pdf), str(output / "public-sample-report-page")],
            check=False,
            capture_output=True,
        )
        copied.extend(path.name for path in sorted(output.glob("public-sample-report-page-*.png")))
    return copied


def _aggregate(results: Sequence[Mapping[str, object]], indexing_ms: float) -> dict[str, object]:
    recalls = [
        float(cast(Mapping[str, object], item["retrieval"])["recall_at_evidence_limit"])
        for item in results
    ]
    completeness = [
        float(cast(Mapping[str, object], item["answer_quality"])["completeness_score"])
        for item in results
    ]
    citation_valid = [
        bool(cast(Mapping[str, object], item["citation_integrity"])["valid"]) for item in results
    ]
    honesty = [
        bool(cast(Mapping[str, object], item["answer_quality"])["honesty_ok"]) for item in results
    ]
    durations = sorted(float(item["duration_ms"]) for item in results)
    failures = Counter(
        str(item["failure_category"]) for item in results if item["failure_category"]
    )
    return {
        "retrieval": {
            "mean_recall_at_evidence_limit": round(sum(recalls) / len(recalls), 4),
            "questions_with_full_recall": sum(value == 1 for value in recalls),
            "questions_with_retrieval_miss": sum(value < 1 for value in recalls),
        },
        "citation_integrity": {
            "structurally_valid_questions": sum(citation_valid),
            "structural_failures": len(citation_valid) - sum(citation_valid),
            "fabricated_identifier_count": sum(
                len(cast(Mapping[str, object], item["citation_integrity"])["errors"])
                for item in results
            ),
        },
        "answer_quality": {
            "mean_completeness_score": round(sum(completeness) / len(completeness), 4),
            "fully_complete_questions": sum(value == 1 for value in completeness),
            "honesty_checks_passed": sum(honesty),
            "unsupported_claim_count": sum(
                len(cast(Mapping[str, object], item["answer_quality"])["unsupported_claims"])
                for item in results
            ),
        },
        "performance": {
            "indexing_duration_ms": indexing_ms,
            "median_question_duration_ms": durations[len(durations) // 2],
            "total_question_duration_ms": round(sum(durations), 2),
        },
        "failure_counts": dict(sorted(failures.items())),
        "dominant_failure": failures.most_common(1)[0][0] if failures else "none",
    }


def _defects(aggregate: Mapping[str, object]) -> list[dict[str, object]]:
    retrieval = cast(Mapping[str, object], aggregate["retrieval"])
    misses = int(retrieval["questions_with_retrieval_miss"])
    defects: list[dict[str, object]] = []
    if misses:
        defects.append(
            {
                "category": "query construction failure",
                "observed": (
                    "The full natural-language question is used as one exact FTS phrase; "
                    f"{misses} questions missed required evidence."
                ),
                "expected": (
                    "Ordinary questions retrieve required evidence already present in "
                    "normalized text."
                ),
                "frequency": misses,
                "severity": "high",
                "user_impact": (
                    "Archiv reports missing evidence and produces incomplete broad answers."
                ),
                "smallest_corrective_layer": (
                    "deterministic query construction before existing FTS retrieval"
                ),
            }
        )
    defects.append(
        {
            "category": "source navigation friction",
            "observed": (
                "Citations expose source names and locators but no bounded "
                "source-location command."
            ),
            "expected": "A user can move from a citation to its source and native location.",
            "frequency": "all cited answers",
            "severity": "medium",
            "user_impact": "Independent verification requires manual storage inspection.",
            "smallest_corrective_layer": "safe source-location command",
        }
    )
    return defects


def _markdown(summary: Mapping[str, object]) -> str:
    aggregate = cast(Mapping[str, object], summary["aggregate"])
    retrieval = cast(Mapping[str, object], aggregate["retrieval"])
    citations = cast(Mapping[str, object], aggregate["citation_integrity"])
    quality = cast(Mapping[str, object], aggregate["answer_quality"])
    performance = cast(Mapping[str, object], aggregate["performance"])
    lines = [
        "# Archiv public field-trial report",
        "",
        (
            "Deterministic public-safe baseline; the fixture model does not measure "
            "real-model quality."
        ),
        "",
        f"- Corpus documents: {summary['corpus_document_count']}",
        f"- Benchmark questions: {summary['question_count']}",
        f"- Full-recall questions: {retrieval['questions_with_full_recall']}",
        f"- Retrieval misses: {retrieval['questions_with_retrieval_miss']}",
        f"- Mean recall: {retrieval['mean_recall_at_evidence_limit']}",
        f"- Structurally valid citations: {citations['structurally_valid_questions']}",
        f"- Fabricated citation identifiers: {citations['fabricated_identifier_count']}",
        f"- Mean completeness: {quality['mean_completeness_score']}",
        f"- Unsupported claims: {quality['unsupported_claim_count']}",
        f"- Median question latency: {performance['median_question_duration_ms']} ms",
        f"- Dominant failure: {aggregate['dominant_failure']}",
        "",
        "## Confirmed defects",
        "",
    ]
    for defect in cast(Sequence[Mapping[str, object]], summary["defects"]):
        lines.extend(
            [
                f"### {defect['category']}",
                "",
                f"- Observed: {defect['observed']}",
                f"- Expected: {defect['expected']}",
                f"- Frequency: {defect['frequency']}",
                f"- Severity: {defect['severity']}",
                f"- User impact: {defect['user_impact']}",
                f"- Smallest corrective layer: {defect['smallest_corrective_layer']}",
                "",
            ]
        )
    return "\n".join(lines)


def scan_safe_artifacts(root: Path, forbidden: Sequence[str] = ()) -> list[str]:
    errors: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(value and value in text for value in forbidden):
            errors.append(f"forbidden private value found in {path.name}")
        if re.search(r"/(home|Users)/[^/\s]+/", text):
            errors.append(f"absolute user path found in {path.name}")
    return errors
