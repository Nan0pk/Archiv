"""Bounded local OCR engine runners and exact model evidence."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from archiv._ocr_benchmark_core import (
    KRAKEN_BLOCK_EVIDENCE,
    RAPIDOCR_EVIDENCE,
    TIMEOUT_SECONDS,
    URD_NAW_EVIDENCE,
    CandidateExecution,
    CandidateRunner,
    EngineText,
    FixtureRecord,
    OcrBenchmarkError,
    normalize_lines,
)
from archiv.hashing import sha256_file


def _tool_output(executable: str, arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise OcrBenchmarkError((completed.stderr or completed.stdout).strip()[:500])
    return completed.stdout.strip() or completed.stderr.strip()


def language_inventory(executable: str) -> tuple[Path | None, list[str]]:
    lines = [
        line.strip()
        for line in _tool_output(executable, ["--list-langs"]).splitlines()
        if line.strip()
    ]
    header = lines[0] if lines and lines[0].lower().startswith("list of") else ""
    match = re.search(r'in "([^"]+)"', header)
    directory = Path(match.group(1)).expanduser().resolve() if match else None
    languages = [line for line in lines if not line.lower().startswith("list of")]
    return directory, sorted(languages)


def default_candidates(available: Sequence[str]) -> list[str]:
    """Return the bounded Tesseract matrix supported by installed models."""

    candidates = [value for value in ("eng", "ara", "urd") if value in available]
    if all(value in available for value in ("eng", "ara", "urd")):
        candidates.append("eng+ara+urd")
    if "urd_naw" in available:
        candidates.append("urd_naw")
        if all(value in available for value in ("eng", "ara")):
            candidates.append("eng+ara+urd_naw")
    return candidates


def _model_evidence(directory: Path | None, candidate: str) -> dict[str, object]:
    files: list[dict[str, object]] = []
    warnings: list[str] = []
    for language in candidate.split("+"):
        path = directory / f"{language}.traineddata" if directory else None
        if path is None or not path.is_file():
            warnings.append(f"traineddata not found for {language}")
            continue
        evidence: dict[str, object] = {
            "language": language,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if language == "urd_naw":
            evidence["upstream"] = URD_NAW_EVIDENCE
        files.append(evidence)
    return {
        "files": files,
        "total_bytes": sum(cast(int, item["bytes"]) for item in files),
        "warnings": warnings,
        "license_evidence": (
            URD_NAW_EVIDENCE
            if "urd_naw" in candidate.split("+")
            else "distribution-package evidence required"
        ),
    }


def _run_process(
    command: Sequence[str],
    work_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], float, int | None]:
    metrics = work_dir / f"process-metrics-{time.monotonic_ns()}.txt"
    wrapped = list(command)
    if Path("/usr/bin/time").is_file():
        wrapped = ["/usr/bin/time", "-f", "%M", "-o", str(metrics), *wrapped]
    started = time.perf_counter()
    completed = subprocess.run(
        wrapped,
        capture_output=True,
        check=False,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=dict(environment) if environment is not None else None,
    )
    elapsed = time.perf_counter() - started
    peak_rss = None
    if metrics.is_file():
        try:
            peak_rss = int(metrics.read_text(encoding="utf-8").strip())
        except ValueError:
            peak_rss = None
        metrics.unlink(missing_ok=True)
    return completed, elapsed, peak_rss


def tesseract_runner(candidate: str) -> CandidateRunner:
    def run(
        fixtures: Sequence[FixtureRecord],
        output_dir: Path,
    ) -> CandidateExecution:
        executable = shutil.which("tesseract")
        if executable is None:
            return CandidateExecution(
                "unavailable",
                "tesseract",
                candidate,
                {},
                {},
                {},
                "Tesseract executable not installed",
            )
        try:
            directory, available = language_inventory(executable)
            missing = [value for value in candidate.split("+") if value not in available]
            if missing:
                return CandidateExecution(
                    "unavailable",
                    "tesseract",
                    candidate,
                    {"available_languages": available},
                    {},
                    {},
                    f"language models unavailable: {', '.join(missing)}",
                )
            environment = os.environ.copy()
            environment.setdefault("OMP_THREAD_LIMIT", "2")
            outputs: dict[str, EngineText] = {}
            for fixture in fixtures:
                command = [
                    executable,
                    str(fixture.image_path),
                    "stdout",
                    "-l",
                    candidate,
                    "--psm",
                    "6",
                ]
                completed, elapsed, peak_rss = _run_process(
                    command,
                    output_dir,
                    environment,
                )
                if completed.returncode != 0:
                    raise OcrBenchmarkError(
                        f"Tesseract exited with {completed.returncode}: "
                        f"{(completed.stderr or completed.stdout).strip()[:500]}"
                    )
                outputs[fixture.fixture_id] = EngineText(
                    completed.stdout,
                    normalize_lines(completed.stdout),
                    elapsed,
                    peak_rss,
                    diagnostic=completed.stderr.strip()[:500],
                )
            path = Path(executable).resolve()
            models = _model_evidence(directory, candidate)
            model_bytes = models.get("total_bytes", 0)
            return CandidateExecution(
                "succeeded",
                "tesseract",
                candidate,
                {
                    "version": _tool_output(executable, ["--version"]).splitlines()[0],
                    "executable_path": str(path),
                    "executable_sha256": sha256_file(path),
                    "executable_bytes": path.stat().st_size,
                    "measured_installation_footprint_bytes": (
                        path.stat().st_size + (model_bytes if isinstance(model_bytes, int) else 0)
                    ),
                    "footprint_scope": "executable plus selected traineddata files",
                    "available_languages": available,
                },
                models,
                outputs,
            )
        except (OSError, subprocess.SubprocessError, OcrBenchmarkError) as error:
            return CandidateExecution(
                "failed",
                "tesseract",
                candidate,
                {},
                {},
                {},
                str(error),
            )

    return run


def rapidocr_runner(
    fixtures: Sequence[FixtureRecord],
    output_dir: Path,
) -> CandidateExecution:
    try:
        rapidocr_version = importlib.metadata.version("rapidocr")
        runtime_version = importlib.metadata.version("onnxruntime")
    except importlib.metadata.PackageNotFoundError as error:
        return CandidateExecution(
            "unavailable",
            "rapidocr",
            "ppocrv5-arabic-mobile",
            RAPIDOCR_EVIDENCE,
            {},
            {},
            str(error),
        )
    request = output_dir / "rapidocr-request.json"
    response = output_dir / "rapidocr-response.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "model_root": str(output_dir / "rapidocr-models"),
                "fixtures": [
                    {
                        "fixture_id": fixture.fixture_id,
                        "image_path": str(fixture.image_path),
                    }
                    for fixture in fixtures
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "archiv.ocr_rapidocr_adapter",
        "--request",
        str(request),
        "--response",
        str(response),
    ]
    completed, elapsed, peak_rss = _run_process(command, output_dir, os.environ.copy())
    evidence = {
        **RAPIDOCR_EVIDENCE,
        "rapidocr_version": rapidocr_version,
        "onnxruntime_version": runtime_version,
        "total_elapsed_seconds": round(elapsed, 6),
        "peak_rss_kib": peak_rss,
    }
    if completed.returncode != 0 or not response.is_file():
        return CandidateExecution(
            "failed",
            "rapidocr",
            "ppocrv5-arabic-mobile",
            evidence,
            {},
            {},
            (completed.stderr or completed.stdout or "adapter produced no response")[:1000],
        )
    try:
        payload_value: object = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(payload_value, dict):
            raise OcrBenchmarkError("RapidOCR adapter response is invalid")
        payload = cast(dict[str, object], payload_value)
        if payload.get("status") != "succeeded":
            raise OcrBenchmarkError("RapidOCR adapter response is invalid")
        values = payload.get("results")
        if not isinstance(values, list):
            raise OcrBenchmarkError("RapidOCR adapter results are invalid")
        result_values = cast(list[object], values)
        outputs: dict[str, EngineText] = {}
        per_fixture = elapsed / max(1, len(result_values))
        for value in result_values:
            if not isinstance(value, dict):
                raise OcrBenchmarkError("RapidOCR result is invalid")
            item = cast(dict[str, object], value)
            fixture_id = item.get("fixture_id")
            text = item.get("text")
            lines = item.get("lines")
            if not isinstance(fixture_id, str) or not isinstance(text, str):
                raise OcrBenchmarkError("RapidOCR text result is invalid")
            if not isinstance(lines, list):
                raise OcrBenchmarkError("RapidOCR line result is invalid")
            line_values = cast(list[object], lines)
            if not all(isinstance(line, str) for line in line_values):
                raise OcrBenchmarkError("RapidOCR line result is invalid")
            outputs[fixture_id] = EngineText(
                text,
                tuple(cast(list[str], line_values)),
                per_fixture,
                peak_rss,
                item.get("coordinates"),
                item.get("confidence"),
            )
        adapter_evidence = payload.get("evidence", {})
        models = payload.get("models", {})
        if not isinstance(adapter_evidence, dict) or not isinstance(models, dict):
            raise OcrBenchmarkError("RapidOCR evidence is invalid")
        return CandidateExecution(
            "succeeded",
            "rapidocr",
            "ppocrv5-arabic-mobile",
            {**evidence, **cast(dict[str, object], adapter_evidence)},
            cast(dict[str, object], models),
            outputs,
        )
    except (OSError, ValueError, OcrBenchmarkError) as error:
        return CandidateExecution(
            "failed",
            "rapidocr",
            "ppocrv5-arabic-mobile",
            evidence,
            {},
            {},
            str(error),
        )


def blocked_kraken_runner(
    fixtures: Sequence[FixtureRecord],
    output_dir: Path,
) -> CandidateExecution:
    del fixtures, output_dir
    return CandidateExecution(
        "blocked",
        "kraken",
        "printed-urdu-model-unverified",
        KRAKEN_BLOCK_EVIDENCE,
        {},
        {},
        cast(str, KRAKEN_BLOCK_EVIDENCE["reason"]),
    )
