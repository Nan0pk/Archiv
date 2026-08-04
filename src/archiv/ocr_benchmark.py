"""Reproducible local OCR engine comparison on lawful multilingual fixtures."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from archiv._ocr_benchmark_core import (
    SCHEMA_VERSION,
    CandidateExecution,
    CandidateRunner,
    EngineText,
    FixtureRecord,
    OcrBenchmarkError,
    edit_counts,
    normalize_lines,
    normalize_text,
    score_text,
)
from archiv._ocr_benchmark_corpus import _fixture_record_from_entry, build_corpus
from archiv._ocr_benchmark_engines import (
    _blocked_kraken_runner,
    _language_inventory,
    _rapidocr_runner,
    _tesseract_runner,
    default_candidates,
)
from archiv._ocr_benchmark_results import (
    _score_execution,
    _summary_markdown,
    rank_candidates,
    route_evidence,
    sanitized_summary,
)
from archiv.hashing import sha256_file

__all__ = [
    "CandidateExecution",
    "CandidateRunner",
    "EngineText",
    "FixtureRecord",
    "OcrBenchmarkError",
    "_fixture_record_from_entry",
    "_score_execution",
    "default_candidates",
    "edit_counts",
    "normalize_lines",
    "normalize_text",
    "rank_candidates",
    "route_evidence",
    "run_benchmark",
    "sanitized_summary",
    "score_text",
    "sha256_file",
]


def _candidate_plan(
    engines: Sequence[str],
    candidates: Sequence[str] | None,
) -> list[tuple[str, CandidateRunner]]:
    plan: list[tuple[str, CandidateRunner]] = []
    for engine in engines:
        if engine == "tesseract":
            available: list[str] = []
            executable = shutil.which("tesseract")
            if executable is not None:
                with contextlib.suppress(OSError, subprocess.SubprocessError, OcrBenchmarkError):
                    _, available = _language_inventory(executable)
            selected = list(candidates) if candidates else default_candidates(available)
            if not selected:
                selected = [
                    "eng",
                    "ara",
                    "urd",
                    "eng+ara+urd",
                    "urd_naw",
                    "eng+ara+urd_naw",
                ]
            plan.extend(
                (f"tesseract:{candidate}", _tesseract_runner(candidate)) for candidate in selected
            )
        elif engine == "rapidocr":
            plan.append(("rapidocr:ppocrv5-arabic-mobile", _rapidocr_runner))
        elif engine == "kraken":
            plan.append(("kraken:printed-urdu", _blocked_kraken_runner))
        else:
            raise OcrBenchmarkError(f"unsupported benchmark engine: {engine}")
    return plan


def _read_first(paths: Sequence[Path]) -> str | None:
    for path in paths:
        try:
            value = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if value:
            return value[:500]
    return None


def _cpu_model() -> str | None:
    try:
        lines = (
            Path("/proc/cpuinfo")
            .read_text(
                encoding="utf-8",
                errors="replace",
            )
            .splitlines()
        )
    except OSError:
        return None
    for line in lines:
        if line.lower().startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()[:500]
    return None


def run_benchmark(
    output_dir: Path,
    candidates: Sequence[str] | None = None,
    engines: Sequence[str] | None = None,
    private_corpus: Path | None = None,
    runner_overrides: Mapping[str, CandidateRunner] | None = None,
) -> dict[str, object]:
    """Generate the corpus, run the fixed matrix, and save inspectable evidence."""

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus(output_dir, private_corpus)
    records_value = corpus.pop("records")
    if not isinstance(records_value, list) or not all(
        isinstance(item, FixtureRecord) for item in records_value
    ):
        raise OcrBenchmarkError("generated corpus records are invalid")
    fixtures = cast(list[FixtureRecord], records_value)
    selected_engines = list(engines or ("tesseract",))
    if not selected_engines:
        raise OcrBenchmarkError("at least one OCR engine must be selected")
    overrides = dict(runner_overrides or {})
    results = [
        _score_execution(
            candidate_id,
            overrides.get(candidate_id, runner)(fixtures, output_dir),
            fixtures,
        )
        for candidate_id, runner in _candidate_plan(selected_engines, candidates)
    ]
    ranking = rank_candidates(results)
    recommended = next(
        (item["candidate_id"] for item in ranking if item["rank"] == 1),
        None,
    )
    private_count = sum(1 for fixture in fixtures if fixture.private)
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": (
            "Lawful generated and explicitly local/private corpus only; "
            "not a universal accuracy claim."
        ),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
            "cpu_model": _cpu_model(),
            "machine_product": _read_first(
                (
                    Path("/sys/class/dmi/id/product_name"),
                    Path("/sys/firmware/devicetree/base/model"),
                )
            ),
            "network_policy": os.environ.get(
                "ARCHIV_OCR_BENCHMARK_NETWORK",
                "not_independently_restricted",
            ),
        },
        "target_hardware_status": (
            "environment recorded; operator must confirm it is the target HP Victus Fedora machine"
        ),
        "public_fixture_count": len(fixtures) - private_count,
        "private_fixture_count": private_count,
        "corpus_manifest_sha256": corpus["manifest_sha256"],
        "engines": selected_engines,
        "candidate_results": results,
        "ranking": ranking,
        "recommended_candidate": recommended,
        "route_evidence": route_evidence(results),
        "product_policy": {
            "normal_ingestion_configuration_changed": False,
            "ocr_remains_derived_evidence": True,
            "native_text_overwritten": False,
            "automatic_indexing_threshold": None,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    shareable_path = output_dir / "shareable-summary.json"
    shareable_path.write_text(
        json.dumps(
            sanitized_summary(report),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary_path = output_dir / "summary.md"
    summary_path.write_text(_summary_markdown(report), encoding="utf-8")
    report.update(
        {
            "report_path": str(report_path),
            "report_sha256": sha256_file(report_path),
            "shareable_summary_path": str(shareable_path),
            "shareable_summary_sha256": sha256_file(shareable_path),
            "summary_path": str(summary_path),
        }
    )
    return report
