"""Small reproducible OCR benchmark for lawful synthetic multilingual fixtures."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from archiv.hashing import sha256_file

SCHEMA_VERSION = "1"
TIMEOUT_SECONDS = 60


class OcrBenchmarkError(RuntimeError):
    """The benchmark could not run safely or completely."""


@dataclass(frozen=True)
class EditCounts:
    substitutions: int
    deletions: int
    insertions: int

    @property
    def distance(self) -> int:
        return self.substitutions + self.deletions + self.insertions


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    language: str
    text: str
    font_role: str
    direction: str
    language_tag: str
    degraded: bool = False


FIXTURES = (
    Fixture("english-clean", "English", "Archiv verifies local evidence 2026.", "eng", "ltr", "en"),
    Fixture(
        "arabic-clean",
        "Arabic Naskh",
        "أرشيف يحفظ الأدلة المحلية ٢٠٢٦.",
        "ara",
        "rtl",
        "ar",
    ),
    Fixture(
        "urdu-clean",
        "Urdu Nastaliq",
        "آرکائیو مقامی ثبوت محفوظ رکھتا ہے ۲۰۲۶۔",
        "urd",
        "rtl",
        "ur",
    ),
    Fixture(
        "mixed-degraded",
        "Mixed Urdu and English degraded scan",
        "Archiv 2026 مقامی evidence محفوظ رکھتا ہے۔",
        "urd",
        "rtl",
        "ur",
        True,
    ),
)

FONT_ENV = {
    "eng": "ARCHIV_OCR_BENCHMARK_FONT_ENG",
    "ara": "ARCHIV_OCR_BENCHMARK_FONT_ARA",
    "urd": "ARCHIV_OCR_BENCHMARK_FONT_URD",
}
FONT_NAMES = {
    "eng": ("NotoSans-Regular.ttf", "DejaVuSans.ttf"),
    "ara": ("NotoNaskhArabic-Regular.ttf", "NotoSansArabic-Regular.ttf", "DejaVuSans.ttf"),
    "urd": ("NotoNastaliqUrdu-Regular.ttf", "NotoNaskhArabic-Regular.ttf"),
}


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def edit_counts[T](reference: Sequence[T], hypothesis: Sequence[T]) -> EditCounts:
    """Return deterministic Levenshtein substitution/deletion/insertion counts."""

    distance = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for row in range(len(reference) + 1):
        distance[row][0] = row
    for column in range(len(hypothesis) + 1):
        distance[0][column] = column
    for row in range(1, len(reference) + 1):
        for column in range(1, len(hypothesis) + 1):
            substitution = int(reference[row - 1] != hypothesis[column - 1])
            distance[row][column] = min(
                distance[row - 1][column - 1] + substitution,
                distance[row - 1][column] + 1,
                distance[row][column - 1] + 1,
            )

    substitutions = deletions = insertions = 0
    row, column = len(reference), len(hypothesis)
    while row or column:
        if row and column and reference[row - 1] == hypothesis[column - 1]:
            row -= 1
            column -= 1
        elif row and column and distance[row - 1][column - 1] + 1 == distance[row][column]:
            substitutions += 1
            row -= 1
            column -= 1
        elif row and distance[row - 1][column] + 1 == distance[row][column]:
            deletions += 1
            row -= 1
        else:
            insertions += 1
            column -= 1
    return EditCounts(substitutions, deletions, insertions)


def _category(text: str, prefix: str) -> list[str]:
    return [character for character in text if unicodedata.category(character).startswith(prefix)]


def score_text(reference: str, hypothesis: str) -> dict[str, object]:
    """Score OCR text without treating engine confidence as correctness."""

    reference = normalize_text(reference)
    hypothesis = normalize_text(hypothesis)
    character_edits = edit_counts(list(reference), list(hypothesis))
    reference_words = reference.split()
    word_edits = edit_counts(reference_words, hypothesis.split())
    punctuation_edits = edit_counts(_category(reference, "P"), _category(hypothesis, "P"))
    numeral_edits = edit_counts(_category(reference, "N"), _category(hypothesis, "N"))
    return {
        "reference": reference,
        "hypothesis": hypothesis,
        "character_count": len(reference),
        "word_count": len(reference_words),
        "cer": round(character_edits.distance / max(1, len(reference)), 6),
        "wer": round(word_edits.distance / max(1, len(reference_words)), 6),
        "character_edits": {
            "substitutions": character_edits.substitutions,
            "deletions": character_edits.deletions,
            "insertions": character_edits.insertions,
        },
        "word_edits": {
            "substitutions": word_edits.substitutions,
            "deletions": word_edits.deletions,
            "insertions": word_edits.insertions,
        },
        "punctuation_error_rate": round(
            punctuation_edits.distance / max(1, len(_category(reference, "P"))), 6
        ),
        "numeral_error_rate": round(
            numeral_edits.distance / max(1, len(_category(reference, "N"))), 6
        ),
        "omitted_line": bool(reference and not hypothesis),
        "hallucinated_characters": character_edits.insertions,
    }


def _find_font(role: str) -> Path:
    configured = os.environ.get(FONT_ENV[role], "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise OcrBenchmarkError(f"configured benchmark font does not exist: {path}")
    roots = (Path("/usr/share/fonts"), Path.home() / ".local/share/fonts")
    for name in FONT_NAMES[role]:
        for root in roots:
            if root.is_dir() and (match := next(root.rglob(name), None)) is not None:
                return match.resolve()
    raise OcrBenchmarkError(f"set {FONT_ENV[role]} to a lawful local font")


def _render(fixture: Fixture, font_path: Path, destination: Path) -> None:
    image = Image.new("RGB", (1800, 360), "white")
    font_size = 76
    while font_size >= 36:
        font = ImageFont.truetype(str(font_path), font_size)
        box = font.getbbox(
            fixture.text,
            direction=fixture.direction,
            language=fixture.language_tag,
        )
        if box[2] - box[0] <= 1640:
            break
        font_size -= 4
    else:
        raise OcrBenchmarkError(f"fixture does not fit using {font_path}")
    draw = ImageDraw.Draw(image)
    position = (1730, 180) if fixture.direction == "rtl" else (70, 180)
    draw.text(
        position,
        fixture.text,
        fill="black",
        font=font,
        anchor="rm" if fixture.direction == "rtl" else "lm",
        direction=fixture.direction,
        language=fixture.language_tag,
    )
    if fixture.degraded:
        image = image.rotate(0.8, Image.Resampling.BICUBIC, fillcolor="white")
        image = image.filter(ImageFilter.GaussianBlur(0.65))
        image = ImageEnhance.Contrast(image).enhance(0.88)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", compress_level=9)


def build_corpus(output_dir: Path) -> dict[str, object]:
    """Generate lawful synthetic fixtures and a content-hashed manifest."""

    fonts = {role: _find_font(role) for role in FONT_ENV}
    entries: list[dict[str, object]] = []
    for fixture in FIXTURES:
        image = output_dir / "fixtures" / f"{fixture.fixture_id}.png"
        _render(fixture, fonts[fixture.font_role], image)
        entries.append(
            {
                "fixture_id": fixture.fixture_id,
                "language": fixture.language,
                "text": fixture.text,
                "degraded": fixture.degraded,
                "image_path": image.relative_to(output_dir).as_posix(),
                "image_sha256": sha256_file(image),
                "font_role": fixture.font_role,
                "font_path": str(fonts[fixture.font_role]),
                "font_sha256": sha256_file(fonts[fixture.font_role]),
            }
        )
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "corpus": "archiv-synthetic-multilingual-ocr",
        "licensing": "Archiv-authored synthetic phrases; local fonts are hashed but not copied.",
        "fixtures": entries,
    }
    path = output_dir / "corpus.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    manifest["manifest_sha256"] = sha256_file(path)
    return manifest


def _tool_output(executable: str, arguments: list[str]) -> str:
    completed = subprocess.run(
        [executable, *arguments], capture_output=True, check=False, text=True, timeout=15
    )
    if completed.returncode != 0:
        raise OcrBenchmarkError((completed.stderr or completed.stdout).strip()[:500])
    return completed.stdout.strip() or completed.stderr.strip()


def _language_inventory(executable: str) -> tuple[Path | None, list[str]]:
    output = _tool_output(executable, ["--list-langs"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    header = (
        lines[0] if lines and lines[0].lower().startswith("list of available languages") else ""
    )
    match = re.search(r'in "([^"]+)"', header)
    tessdata_dir = Path(match.group(1)).expanduser().resolve() if match else None
    languages = [
        line for line in lines if not line.lower().startswith("list of available languages")
    ]
    return tessdata_dir, sorted(languages)


def _model_evidence(
    tessdata_dir: Path | None,
    candidates: Sequence[str],
) -> dict[str, object]:
    languages = sorted({language for candidate in candidates for language in candidate.split("+")})
    files: list[dict[str, object]] = []
    warnings: list[str] = []
    total_bytes = 0
    if tessdata_dir is None:
        warnings.append("Tesseract did not expose its tessdata directory in --list-langs output.")
    else:
        for language in languages:
            path = tessdata_dir / f"{language}.traineddata"
            if not path.is_file():
                warnings.append(f"traineddata file not found for {language}: {path}")
                continue
            size = path.stat().st_size
            total_bytes += size
            files.append(
                {
                    "language": language,
                    "path": str(path),
                    "bytes": size,
                    "sha256": sha256_file(path),
                }
            )
    return {
        "tessdata_dir": str(tessdata_dir) if tessdata_dir else None,
        "files": files,
        "total_bytes": total_bytes,
        "warnings": warnings,
        "license_status": "operator_verification_required",
    }


def default_candidates(available: Sequence[str]) -> list[str]:
    """Return the bounded Tesseract candidate matrix supported by installed models."""

    candidates = [language for language in ("eng", "ara", "urd") if language in available]
    if all(language in available for language in ("eng", "ara", "urd")):
        candidates.append("eng+ara+urd")
    if "urd_naw" in available:
        candidates.append("urd_naw")
        if all(language in available for language in ("eng", "ara")):
            candidates.append("eng+ara+urd_naw")
    return candidates


def _run_tesseract(
    executable: str, image: Path, candidate: str, work_dir: Path
) -> tuple[str, str, float, int | None]:
    """Run Tesseract once; GNU time adds peak RSS when available."""

    command = [executable, str(image), "stdout", "-l", candidate, "--psm", "6"]
    metrics_path = work_dir / "process-metrics.txt"
    time_executable = Path("/usr/bin/time")
    if time_executable.is_file():
        command = [str(time_executable), "-f", "%M", "-o", str(metrics_path), *command]
    environment = os.environ.copy()
    environment.setdefault("OMP_THREAD_LIMIT", "2")
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=environment,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise OcrBenchmarkError(
            f"Tesseract exited with status {completed.returncode}: "
            f"{(completed.stderr or completed.stdout).strip()[:500]}"
        )
    peak_rss = None
    if metrics_path.is_file():
        try:
            peak_rss = int(metrics_path.read_text().strip())
        except ValueError:
            peak_rss = None
        metrics_path.unlink(missing_ok=True)
    return completed.stdout, completed.stderr.strip()[:500], elapsed, peak_rss


def _edit_total(value: object) -> int:
    if not isinstance(value, dict):
        raise OcrBenchmarkError("benchmark edit metrics are invalid")
    metrics = cast(dict[str, object], value)
    total = 0
    for key in ("substitutions", "deletions", "insertions"):
        count = metrics.get(key)
        if not isinstance(count, int):
            raise OcrBenchmarkError("benchmark edit metrics are invalid")
        total += count
    return total


def _aggregate(candidate: str, runs: Sequence[dict[str, object]]) -> dict[str, object]:
    character_errors = word_errors = character_count = word_count = 0
    elapsed = 0.0
    peak_rss: list[int] = []
    for run in runs:
        metrics_value = run["metrics"]
        if not isinstance(metrics_value, dict):
            raise OcrBenchmarkError("benchmark metrics are invalid")
        metrics = cast(dict[str, object], metrics_value)
        character_errors += _edit_total(metrics["character_edits"])
        word_errors += _edit_total(metrics["word_edits"])
        character_count_value = metrics["character_count"]
        word_count_value = metrics["word_count"]
        elapsed_value = run["elapsed_seconds"]
        if not isinstance(character_count_value, int) or not isinstance(word_count_value, int):
            raise OcrBenchmarkError("benchmark counts are invalid")
        if not isinstance(elapsed_value, int | float):
            raise OcrBenchmarkError("benchmark timing is invalid")
        character_count += character_count_value
        word_count += word_count_value
        elapsed += float(elapsed_value)
        peak_value = run["peak_rss_kib"]
        if isinstance(peak_value, int):
            peak_rss.append(peak_value)
    return {
        "candidate": candidate,
        "cer": round(character_errors / max(1, character_count), 6),
        "wer": round(word_errors / max(1, word_count), 6),
        "elapsed_seconds": round(elapsed, 6),
        "peak_rss_kib": max(peak_rss) if peak_rss else None,
    }


def _aggregate_number(item: dict[str, object], key: str) -> float:
    value = item.get(key)
    if not isinstance(value, int | float):
        raise OcrBenchmarkError(f"aggregate {key} is invalid")
    return float(value)


def run_benchmark(output_dir: Path, candidates: Sequence[str] | None = None) -> dict[str, object]:
    """Generate fixtures, measure installed Tesseract candidates, and save JSON evidence."""

    executable = shutil.which("tesseract")
    if executable is None:
        raise OcrBenchmarkError("Tesseract executable not installed")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus(output_dir)
    fixtures_value = corpus["fixtures"]
    if not isinstance(fixtures_value, list):
        raise OcrBenchmarkError("generated corpus manifest is invalid")
    fixtures = cast(list[dict[str, object]], fixtures_value)
    tessdata_dir, available = _language_inventory(executable)
    selected = list(candidates) if candidates else default_candidates(available)
    missing = sorted(
        language
        for candidate in selected
        for language in candidate.split("+")
        if language not in available
    )
    if not selected or missing:
        detail = ", ".join(missing) if missing else "none installed"
        raise OcrBenchmarkError(f"benchmark language models unavailable: {detail}")

    runs: list[dict[str, object]] = []
    for candidate in selected:
        for fixture in fixtures:
            hypothesis, diagnostic, elapsed, peak_rss = _run_tesseract(
                executable,
                output_dir / str(fixture["image_path"]),
                candidate,
                output_dir,
            )
            runs.append(
                {
                    "candidate": candidate,
                    "fixture_id": fixture["fixture_id"],
                    "language": fixture["language"],
                    "image_sha256": fixture["image_sha256"],
                    "elapsed_seconds": round(elapsed, 6),
                    "peak_rss_kib": peak_rss,
                    "diagnostic": diagnostic,
                    "metrics": score_text(str(fixture["text"]), hypothesis),
                }
            )
    aggregates = [
        _aggregate(candidate, [run for run in runs if run["candidate"] == candidate])
        for candidate in selected
    ]
    recommended = min(
        aggregates,
        key=lambda item: (
            _aggregate_number(item, "cer"),
            _aggregate_number(item, "wer"),
            _aggregate_number(item, "elapsed_seconds"),
        ),
    )["candidate"]
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "Synthetic reference corpus only; not a universal accuracy claim.",
        "engine": "tesseract",
        "engine_version": _tool_output(executable, ["--version"]).splitlines()[0],
        "engine_executable_sha256": sha256_file(Path(executable).resolve()),
        "available_languages": available,
        "candidates": selected,
        "recommended_candidate": recommended,
        "model_evidence": _model_evidence(tessdata_dir, selected),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
        },
        "corpus_manifest_sha256": corpus["manifest_sha256"],
        "runs": runs,
        "aggregates": aggregates,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    report["report_sha256"] = sha256_file(report_path)
    return report
