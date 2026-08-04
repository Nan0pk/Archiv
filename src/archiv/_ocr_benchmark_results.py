"""Cross-engine scoring, ranking, sanitization, and summaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from archiv._ocr_benchmark_core import (
    SCHEMA_VERSION,
    CandidateExecution,
    FixtureRecord,
    OcrBenchmarkError,
    _category,
    score_text,
)


def _edit_total(value: object) -> int:
    if not isinstance(value, dict):
        raise OcrBenchmarkError("benchmark edit metrics are invalid")
    metrics = cast(dict[str, object], value)
    counts = [metrics.get(key) for key in ("substitutions", "deletions", "insertions")]
    if not all(isinstance(count, int) for count in counts):
        raise OcrBenchmarkError("benchmark edit metrics are invalid")
    return sum(cast(list[int], counts))


def _aggregate(
    candidate_id: str,
    runs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    errors = {"character": 0, "word": 0, "line": 0}
    counts = {"character": 0, "word": 0, "line": 0}
    elapsed = 0.0
    peaks: list[int] = []
    punctuation = numeral = 0.0
    punctuation_count = numeral_count = 0
    for run in runs:
        value = run.get("metrics")
        if not isinstance(value, dict):
            raise OcrBenchmarkError("benchmark metrics are invalid")
        metrics = cast(dict[str, object], value)
        for name in errors:
            errors[name] += _edit_total(metrics[f"{name}_edits"])
            count = metrics.get(f"{name}_count")
            if not isinstance(count, int):
                raise OcrBenchmarkError("benchmark count is invalid")
            counts[name] += count
        reference = metrics.get("reference")
        if not isinstance(reference, str):
            raise OcrBenchmarkError("benchmark reference is invalid")
        punct_count = len(_category(reference, "P"))
        num_count = len(_category(reference, "N"))
        punct_accuracy = metrics.get("punctuation_accuracy")
        num_accuracy = metrics.get("numeral_accuracy")
        if not isinstance(punct_accuracy, int | float) or not isinstance(
            num_accuracy,
            int | float,
        ):
            raise OcrBenchmarkError("benchmark category accuracy is invalid")
        punctuation += float(punct_accuracy) * punct_count
        numeral += float(num_accuracy) * num_count
        punctuation_count += punct_count
        numeral_count += num_count
        timing = run.get("elapsed_seconds")
        if not isinstance(timing, int | float):
            raise OcrBenchmarkError("benchmark timing is invalid")
        elapsed += float(timing)
        peak = run.get("peak_rss_kib")
        if isinstance(peak, int):
            peaks.append(peak)
    return {
        "candidate_id": candidate_id,
        "fixture_count": len(runs),
        "cer": round(errors["character"] / max(1, counts["character"]), 6),
        "wer": round(errors["word"] / max(1, counts["word"]), 6),
        "reading_order_error_rate": round(
            errors["line"] / max(1, counts["line"]),
            6,
        ),
        "punctuation_accuracy": round(
            punctuation / max(1, punctuation_count),
            6,
        ),
        "numeral_accuracy": round(numeral / max(1, numeral_count), 6),
        "elapsed_seconds": round(elapsed, 6),
        "peak_rss_kib": max(peaks) if peaks else None,
    }


def _score_execution(
    candidate_id: str,
    execution: CandidateExecution,
    fixtures: Sequence[FixtureRecord],
) -> dict[str, object]:
    result: dict[str, object] = {
        "candidate_id": candidate_id,
        "engine": execution.engine,
        "configuration": execution.configuration,
        "status": execution.status,
        "warning": execution.warning,
        "engine_evidence": dict(execution.engine_evidence),
        "model_evidence": dict(execution.model_evidence),
        "runs": [],
        "aggregate": None,
    }
    if execution.status != "succeeded":
        return result
    runs: list[dict[str, object]] = []
    missing: list[str] = []
    for fixture in fixtures:
        output = execution.outputs.get(fixture.fixture_id)
        if output is None:
            missing.append(fixture.fixture_id)
            continue
        runs.append(
            {
                "fixture_id": fixture.fixture_id,
                "language": fixture.language,
                "tags": list(fixture.tags),
                "private": fixture.private,
                "source_kind": fixture.source_kind,
                "image_sha256": fixture.image_sha256,
                "elapsed_seconds": round(output.elapsed_seconds, 6),
                "peak_rss_kib": output.peak_rss_kib,
                "coordinates": output.coordinates,
                "confidence": output.confidence,
                "diagnostic": output.diagnostic,
                "metrics": score_text(
                    fixture.ground_truth,
                    output.text,
                    fixture.expected_lines,
                    output.lines,
                ),
            }
        )
    result["runs"] = runs
    if missing:
        result["status"] = "failed"
        result["warning"] = f"engine omitted fixture results: {', '.join(missing)}"
    else:
        result["aggregate"] = _aggregate(candidate_id, runs)
    return result


def _aggregate_number(item: Mapping[str, object], key: str) -> float:
    aggregate = item.get("aggregate")
    if not isinstance(aggregate, dict):
        raise OcrBenchmarkError("candidate aggregate is unavailable")
    value = aggregate.get(key)
    if not isinstance(value, int | float):
        raise OcrBenchmarkError(f"aggregate {key} is invalid")
    return float(value)


def rank_candidates(results: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Rank success without silently hiding unavailable, failed, or blocked candidates."""

    successful = [
        item
        for item in results
        if item.get("status") == "succeeded" and isinstance(item.get("aggregate"), dict)
    ]
    successful.sort(
        key=lambda item: (
            _aggregate_number(item, "cer"),
            _aggregate_number(item, "wer"),
            _aggregate_number(item, "reading_order_error_rate"),
            _aggregate_number(item, "elapsed_seconds"),
            str(item.get("candidate_id")),
        )
    )
    ranks = {str(item["candidate_id"]): index + 1 for index, item in enumerate(successful)}
    return [
        {
            "candidate_id": item.get("candidate_id"),
            "status": item.get("status"),
            "rank": ranks.get(str(item.get("candidate_id"))),
            "warning": item.get("warning"),
        }
        for item in results
    ]


def _group_aggregate(
    result: Mapping[str, object],
    tags: set[str],
) -> dict[str, float] | None:
    value = result.get("runs")
    if not isinstance(value, list):
        return None
    runs: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        run = cast(dict[str, object], item)
        run_tags = run.get("tags", [])
        if isinstance(run_tags, list) and tags.issubset(
            {tag for tag in run_tags if isinstance(tag, str)}
        ):
            runs.append(run)
    if not runs:
        return None
    aggregate = _aggregate(str(result.get("candidate_id")), runs)
    return {
        "cer": cast(float, aggregate["cer"]),
        "wer": cast(float, aggregate["wer"]),
        "reading_order_error_rate": cast(
            float,
            aggregate["reading_order_error_rate"],
        ),
    }


def route_evidence(results: Sequence[dict[str, object]]) -> dict[str, object]:
    groups = {
        "known_english": {"english", "clean"},
        "known_arabic": {"arabic", "clean"},
        "known_urdu": {"urdu", "clean"},
        "unknown_or_mixed": {"mixed"},
        "degraded_material": {"degraded"},
    }
    routes: dict[str, object] = {}
    for route, tags in groups.items():
        options: list[dict[str, object]] = []
        for result in results:
            if result.get("status") == "succeeded":
                metrics = _group_aggregate(result, tags)
                if metrics is not None:
                    options.append({"candidate_id": result.get("candidate_id"), **metrics})
        options.sort(
            key=lambda item: (
                float(item["cer"]),
                float(item["wer"]),
                str(item["candidate_id"]),
            )
        )
        routes[route] = {
            "best_measured_candidate": (options[0]["candidate_id"] if options else None),
            "measurements": options,
            "automatic_indexing_decision": (
                "not_automated; review measured failures and source risk"
            ),
        }
    return routes


def _sanitize(value: object, key: str = "") -> object:
    if any(token in key.lower() for token in ("path", "root", "directory")):
        return "redacted-local-path"
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def sanitized_summary(report: Mapping[str, object]) -> dict[str, object]:
    """Return aggregates without private text, crops, filenames, paths, or fixture IDs."""

    value = report.get("candidate_results", [])
    if not isinstance(value, list):
        raise OcrBenchmarkError("candidate results are invalid")
    candidates = []
    for item in value:
        if not isinstance(item, dict):
            continue
        candidate = cast(dict[str, object], item)
        aggregate = candidate.get("aggregate")
        candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "engine": candidate.get("engine"),
                "configuration": candidate.get("configuration"),
                "status": candidate.get("status"),
                "warning": candidate.get("warning"),
                "aggregate": aggregate if isinstance(aggregate, dict) else None,
                "engine_evidence": _sanitize(candidate.get("engine_evidence")),
                "model_evidence": _sanitize(candidate.get("model_evidence")),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": report.get("scope"),
        "environment": report.get("environment"),
        "corpus": {
            "public_fixture_count": report.get("public_fixture_count"),
            "private_fixture_count": report.get("private_fixture_count"),
            "private_content_included": False,
            "corpus_manifest_sha256": report.get("corpus_manifest_sha256"),
        },
        "candidate_results": candidates,
        "ranking": report.get("ranking"),
        "route_evidence": report.get("route_evidence"),
        "target_hardware_status": report.get("target_hardware_status"),
    }


def _summary_markdown(report: Mapping[str, object]) -> str:
    value = report.get("candidate_results", [])
    candidates = (
        [cast(dict[str, object], item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )
    lines = [
        "# Archiv OCR engine comparison",
        "",
        str(report.get("scope")),
        "",
        "| Candidate | Status | CER | WER | Reading-order error | Time | Peak RSS |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for candidate in candidates:
        aggregate = candidate.get("aggregate")
        if isinstance(aggregate, dict):
            metrics = cast(dict[str, object], aggregate)
            lines.append(
                f"| `{candidate.get('candidate_id')}` | {candidate.get('status')} | "
                f"{float(metrics['cer']):.1%} | {float(metrics['wer']):.1%} | "
                f"{float(metrics['reading_order_error_rate']):.1%} | "
                f"{float(metrics['elapsed_seconds']):.3f}s | "
                f"{metrics.get('peak_rss_kib')} KiB |"
            )
        else:
            lines.append(
                f"| `{candidate.get('candidate_id')}` | {candidate.get('status')} | "
                "— | — | — | — | — |"
            )
            if candidate.get("warning"):
                lines.append(f"\nWarning: {candidate.get('warning')}\n")
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- Results apply only to this exact corpus and machine.",
            "- Target Fedora measurements remain unresolved until run on that machine.",
            "- No accuracy threshold is invented for automatic indexing.",
            "- OCR remains derived evidence and does not overwrite native extraction.",
            "",
        ]
    )
    return "\n".join(lines)
