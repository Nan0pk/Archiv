"""Reproducible local OCR engine comparison on lawful multilingual fixtures."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from archiv.hashing import sha256_file

SCHEMA_VERSION = "2"
TIMEOUT_SECONDS = 120
MAX_PRIVATE_FIXTURES = 200
MAX_FIXTURE_BYTES = 200 * 1024 * 1024
CandidateStatus = Literal["succeeded", "unavailable", "failed", "blocked"]


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
class FixtureTemplate:
    fixture_id: str
    language: str
    lines: tuple[str, ...]
    font_role: str
    direction: Literal["ltr", "rtl"]
    language_tag: str
    layout: Literal["single", "multiline", "columns", "form"] = "single"
    transform: Literal["clean", "blur", "rotate", "low_contrast", "phone"] = "clean"
    font_variant: int = 0
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixtureRecord:
    fixture_id: str
    language: str
    ground_truth: str
    expected_lines: tuple[str, ...]
    image_path: Path
    image_sha256: str
    tags: tuple[str, ...]
    source_kind: str
    private: bool


@dataclass(frozen=True)
class EngineText:
    text: str
    lines: tuple[str, ...]
    elapsed_seconds: float
    peak_rss_kib: int | None
    coordinates: object | None = None
    confidence: object | None = None
    diagnostic: str = ""


@dataclass(frozen=True)
class CandidateExecution:
    status: CandidateStatus
    engine: str
    configuration: str
    engine_evidence: Mapping[str, object]
    model_evidence: Mapping[str, object]
    outputs: Mapping[str, EngineText]
    warning: str | None = None


class CandidateRunner(Protocol):
    def __call__(
        self,
        fixtures: Sequence[FixtureRecord],
        output_dir: Path,
    ) -> CandidateExecution: ...


PUBLIC_FIXTURES = (
    FixtureTemplate(
        "english-clean",
        "English",
        ("Archiv verifies local evidence 2026.",),
        "eng",
        "ltr",
        "en",
        tags=("english", "clean"),
    ),
    FixtureTemplate(
        "arabic-naskh-clean",
        "Arabic Naskh",
        ("أرشيف يحفظ الأدلة المحلية ٢٠٢٦.",),
        "ara",
        "rtl",
        "ar",
        tags=("arabic", "clean", "naskh", "arabic-numerals"),
    ),
    FixtureTemplate(
        "urdu-nastaliq-primary",
        "Urdu Nastaliq",
        ("آرکائیو مقامی ثبوت محفوظ رکھتا ہے ۲۰۲۶۔",),
        "urd",
        "rtl",
        "ur",
        tags=("urdu", "clean", "nastaliq", "arabic-numerals"),
    ),
    FixtureTemplate(
        "urdu-nastaliq-secondary",
        "Urdu Nastaliq second font",
        ("قابلِ تلاش متن درست ماخذ کے ساتھ محفوظ رہے۔",),
        "urd",
        "rtl",
        "ur",
        font_variant=1,
        tags=("urdu", "clean", "nastaliq", "second-font", "punctuation"),
    ),
    FixtureTemplate(
        "mixed-urdu-english",
        "Mixed Urdu and English",
        ("Archiv 2026 میں local evidence محفوظ ہے۔",),
        "urd",
        "rtl",
        "ur",
        tags=("urdu", "english", "mixed", "western-numerals", "punctuation"),
    ),
    FixtureTemplate(
        "numerals-punctuation",
        "Urdu numerals and punctuation",
        ("فائل نمبر ۱۲۳؛ Page 45، تاریخ ۲۰۲۶-08-04۔",),
        "urd",
        "rtl",
        "ur",
        tags=(
            "urdu",
            "english",
            "mixed",
            "arabic-numerals",
            "western-numerals",
            "punctuation",
        ),
    ),
    FixtureTemplate(
        "urdu-multiline",
        "Urdu multi-line",
        (
            "پہلی سطر میں اصل متن ہے۔",
            "دوسری سطر میں حوالہ ۲۶ ہے۔",
            "تیسری سطر محفوظ رہے۔",
        ),
        "urd",
        "rtl",
        "ur",
        layout="multiline",
        tags=("urdu", "multiline", "reading-order"),
    ),
    FixtureTemplate(
        "urdu-columns",
        "Urdu two-column",
        (
            "دائیں کالم پہلی سطر۔",
            "دائیں کالم دوسری سطر۔",
            "بائیں کالم پہلی سطر۔",
            "بائیں کالم دوسری سطر۔",
        ),
        "urd",
        "rtl",
        "ur",
        layout="columns",
        tags=("urdu", "columns", "reading-order"),
    ),
    FixtureTemplate(
        "labelled-form",
        "Simple labelled form",
        ("نام: احمد خان", "File ID: A-204", "تاریخ: ۰۴ اگست ۲۰۲۶"),
        "urd",
        "rtl",
        "ur",
        layout="form",
        tags=(
            "urdu",
            "english",
            "form",
            "mixed",
            "arabic-numerals",
            "western-numerals",
        ),
    ),
    FixtureTemplate(
        "urdu-blurred",
        "Blurred Urdu scan",
        ("دھندلی نقل میں بھی اصل عبارت دیکھی جائے۔",),
        "urd",
        "rtl",
        "ur",
        transform="blur",
        tags=("urdu", "degraded", "blur"),
    ),
    FixtureTemplate(
        "arabic-rotated",
        "Rotated Arabic scan",
        ("النص العربي المائل يحتاج إلى مراجعة ١٢٣.",),
        "ara",
        "rtl",
        "ar",
        transform="rotate",
        tags=("arabic", "degraded", "rotation", "arabic-numerals"),
    ),
    FixtureTemplate(
        "mixed-low-contrast",
        "Low-contrast mixed scan",
        ("Review 77: کم تضاد والی عبارت۔",),
        "urd",
        "rtl",
        "ur",
        transform="low_contrast",
        tags=("urdu", "english", "mixed", "degraded", "low-contrast"),
    ),
    FixtureTemplate(
        "urdu-phone-photo",
        "Phone-photograph-like Urdu",
        ("فون سے لی گئی تصویر میں زاویہ بدل سکتا ہے۔",),
        "urd",
        "rtl",
        "ur",
        transform="phone",
        tags=("urdu", "degraded", "phone-photo", "perspective"),
    ),
)

FONT_ENV = {
    "eng": "ARCHIV_OCR_BENCHMARK_FONT_ENG",
    "ara": "ARCHIV_OCR_BENCHMARK_FONT_ARA",
    "urd": "ARCHIV_OCR_BENCHMARK_FONTS_URD",
}
FONT_NAMES = {
    "eng": ("NotoSans-Regular.ttf", "DejaVuSans.ttf"),
    "ara": (
        "NotoNaskhArabic-Regular.ttf",
        "NotoSansArabic-Regular.ttf",
        "DejaVuSans.ttf",
    ),
    "urd": (
        "NotoNastaliqUrdu-Regular.ttf",
        "Nafees.ttf",
        "NafeesWebNaskh.ttf",
        "Jameel Noori Nastaleeq.ttf",
        "NotoNaskhArabic-Regular.ttf",
    ),
}

URD_NAW_EVIDENCE: dict[str, object] = {
    "repository": "tesseract-ocr/tessdata_contrib",
    "commit": "1b7ada6f9ed0e165f06b3212500e1433fdf4dfc7",
    "path": "urd_naw/best/urd_naw.traineddata",
    "git_blob_sha1": "cb79560e7c97ea56082d1e285ffa3dcc319b1113",
    "repository_license": "Apache-2.0",
    "training_corpus_provenance": (
        "upstream README links a dataset but does not enumerate source rights"
    ),
    "redistributed_by_archiv": False,
}
RAPIDOCR_EVIDENCE: dict[str, object] = {
    "project": "RapidAI/RapidOCR",
    "package": "rapidocr",
    "expected_version": "3.9.2",
    "runtime_package": "onnxruntime",
    "expected_runtime_version": "1.27.0",
    "configuration": "PP-OCRv5 Arabic mobile recognition with ONNX Runtime CPU",
    "code_license": "Apache-2.0",
    "model_notice": (
        "RapidOCR states OCR model copyright is held by Baidu; weights are not "
        "redistributed by Archiv"
    ),
}
KRAKEN_BLOCK_EVIDENCE: dict[str, object] = {
    "project": "mittagessen/kraken",
    "status": "blocked",
    "reason": (
        "No specific printed-Urdu model with exact identity, hash, and explicit "
        "evaluation terms was verified."
    ),
    "weights_downloaded": False,
}


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def normalize_lines(text: str) -> tuple[str, ...]:
    return tuple(
        normalized
        for line in text.splitlines()
        if (normalized := normalize_text(line))
    )


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
        elif (
            row
            and column
            and distance[row - 1][column - 1] + 1 == distance[row][column]
        ):
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
    return [
        character
        for character in text
        if unicodedata.category(character).startswith(prefix)
    ]


def _edit_dict(value: EditCounts) -> dict[str, int]:
    return {
        "substitutions": value.substitutions,
        "deletions": value.deletions,
        "insertions": value.insertions,
    }


def score_text(
    reference: str,
    hypothesis: str,
    expected_lines: Sequence[str] | None = None,
    observed_lines: Sequence[str] | None = None,
) -> dict[str, object]:
    """Score OCR text without treating engine confidence as correctness."""

    reference = normalize_text(reference)
    hypothesis = normalize_text(hypothesis)
    characters = edit_counts(list(reference), list(hypothesis))
    reference_words = reference.split()
    words = edit_counts(reference_words, hypothesis.split())
    reference_punctuation = _category(reference, "P")
    reference_numerals = _category(reference, "N")
    punctuation = edit_counts(reference_punctuation, _category(hypothesis, "P"))
    numerals = edit_counts(reference_numerals, _category(hypothesis, "N"))
    reference_lines = tuple(
        normalize_text(line) for line in (expected_lines or (reference,))
    )
    hypothesis_lines = tuple(
        normalize_text(line)
        for line in (observed_lines or normalize_lines(hypothesis))
    )
    lines = edit_counts(reference_lines, hypothesis_lines)
    punctuation_error = punctuation.distance / max(1, len(reference_punctuation))
    numeral_error = numerals.distance / max(1, len(reference_numerals))
    return {
        "reference": reference,
        "hypothesis": hypothesis,
        "character_count": len(reference),
        "word_count": len(reference_words),
        "line_count": len(reference_lines),
        "cer": round(characters.distance / max(1, len(reference)), 6),
        "wer": round(words.distance / max(1, len(reference_words)), 6),
        "character_edits": _edit_dict(characters),
        "word_edits": _edit_dict(words),
        "line_edits": _edit_dict(lines),
        "reading_order_error_rate": round(
            lines.distance / max(1, len(reference_lines)),
            6,
        ),
        "punctuation_error_rate": round(punctuation_error, 6),
        "numeral_error_rate": round(numeral_error, 6),
        "punctuation_accuracy": round(max(0.0, 1.0 - punctuation_error), 6),
        "numeral_accuracy": round(max(0.0, 1.0 - numeral_error), 6),
        "omitted_line": bool(reference and not hypothesis),
        "omitted_lines": lines.deletions,
        "inserted_lines": lines.insertions,
        "hallucinated_characters": characters.insertions,
    }


def _find_fonts(role: str) -> list[Path]:
    configured = os.environ.get(FONT_ENV[role], "").strip()
    found = [
        Path(value).expanduser().resolve()
        for value in configured.split(os.pathsep)
        if value
    ]
    missing = [path for path in found if not path.is_file()]
    if missing:
        raise OcrBenchmarkError(f"configured benchmark font does not exist: {missing[0]}")
    for name in FONT_NAMES[role]:
        for root in (Path("/usr/share/fonts"), Path.home() / ".local/share/fonts"):
            if root.is_dir():
                for match in root.rglob(name):
                    path = match.resolve()
                    if path not in found:
                        found.append(path)
    if not found:
        raise OcrBenchmarkError(f"set {FONT_ENV[role]} to a lawful local font")
    return found


def _font_for(
    fonts: Mapping[str, Sequence[Path]],
    fixture: FixtureTemplate,
) -> Path:
    choices = fonts[fixture.font_role]
    if fixture.font_variant >= len(choices):
        raise OcrBenchmarkError(
            f"fixture {fixture.fixture_id} requires another {fixture.font_role} font; "
            f"set {FONT_ENV[fixture.font_role]} to {os.pathsep}-separated paths"
        )
    return choices[fixture.font_variant]


def _draw_line(
    draw: ImageDraw.ImageDraw,
    fixture: FixtureTemplate,
    font: ImageFont.FreeTypeFont,
    text: str,
    x: int,
    y: int,
) -> None:
    if fixture.direction == "rtl":
        draw.text(
            (x, y),
            text,
            fill="black",
            font=font,
            anchor="ra",
            direction="rtl",
            language=fixture.language_tag,
        )
    else:
        draw.text(
            (x, y),
            text,
            fill="black",
            font=font,
            anchor="la",
            direction="ltr",
        )


def _render(
    fixture: FixtureTemplate,
    font_path: Path,
    destination: Path,
) -> dict[str, object]:
    width, height = (1800, 900) if fixture.layout in {"columns", "form"} else (1800, 640)
    image = Image.new("RGB", (width, height), "white")
    font = ImageFont.truetype(str(font_path), 58)
    draw = ImageDraw.Draw(image)
    if fixture.layout == "columns":
        for index, line in enumerate(fixture.lines[:2]):
            _draw_line(draw, fixture, font, line, 1680, 180 + index * 150)
        for index, line in enumerate(fixture.lines[2:]):
            _draw_line(draw, fixture, font, line, 820, 180 + index * 150)
        draw.line((900, 90, 900, 600), fill="gray", width=2)
    else:
        for index, line in enumerate(fixture.lines):
            _draw_line(draw, fixture, font, line, 1680, 150 + index * 150)
            if fixture.layout == "form":
                top = 85 + index * 150
                draw.rectangle((100, top, 1700, top + 120), outline="gray", width=2)

    settings: dict[str, object] = {"name": fixture.transform}
    if fixture.transform == "blur":
        image = image.filter(ImageFilter.GaussianBlur(1.35))
        settings["radius"] = 1.35
    elif fixture.transform == "rotate":
        image = image.rotate(2.3, Image.Resampling.BICUBIC, fillcolor="white")
        settings["degrees"] = 2.3
    elif fixture.transform == "low_contrast":
        image = ImageEnhance.Contrast(image).enhance(0.42)
        settings["contrast_factor"] = 0.42
    elif fixture.transform == "phone":
        quad = (70, 45, width - 5, 5, width - 95, height - 15, 5, height - 70)
        image = image.transform(
            image.size,
            Image.Transform.QUAD,
            quad,
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        ).filter(ImageFilter.GaussianBlur(0.45))
        settings.update({"quad": list(quad), "blur_radius": 0.45})
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", compress_level=9)
    return settings


def _record(
    entry: Mapping[str, object],
    root: Path,
    private: bool,
) -> FixtureRecord:
    strings: dict[str, str] = {}
    for key in ("fixture_id", "language", "ground_truth", "image_path", "source_kind"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise OcrBenchmarkError(f"corpus fixture {key} must be a non-empty string")
        strings[key] = value
    relative = Path(strings["image_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise OcrBenchmarkError("corpus image_path must remain inside its corpus directory")
    image = (root / relative).resolve()
    if root.resolve() not in image.parents or not image.is_file():
        raise OcrBenchmarkError(f"corpus image is unavailable: {relative}")
    if image.stat().st_size > MAX_FIXTURE_BYTES:
        raise OcrBenchmarkError(f"corpus image exceeds {MAX_FIXTURE_BYTES} bytes")
    lines = entry.get("expected_lines")
    tags = entry.get("tags", [])
    if not isinstance(lines, list) or not lines or not all(
        isinstance(value, str) and value.strip() for value in lines
    ):
        raise OcrBenchmarkError("corpus expected_lines must contain non-empty strings")
    if not isinstance(tags, list) or not all(isinstance(value, str) for value in tags):
        raise OcrBenchmarkError("corpus tags must be strings")
    if private and not all(
        isinstance(entry.get(key), str) and cast(str, entry[key]).strip()
        for key in ("lawful_basis", "generation_method")
    ):
        raise OcrBenchmarkError(
            "private fixtures require lawful_basis and generation_method"
        )
    return FixtureRecord(
        fixture_id=strings["fixture_id"],
        language=strings["language"],
        ground_truth=strings["ground_truth"],
        expected_lines=tuple(cast(list[str], lines)),
        image_path=image,
        image_sha256=sha256_file(image),
        tags=tuple(cast(list[str], tags)),
        source_kind=strings["source_kind"],
        private=private,
    )


def _fixture_record_from_entry(
    entry: Mapping[str, object],
    root: Path,
    private: bool,
) -> FixtureRecord:
    """Validate one corpus entry. Kept separate for focused tests."""

    return _record(entry, root, private)


def build_corpus(
    output_dir: Path,
    private_corpus: Path | None = None,
) -> dict[str, object]:
    """Generate public fixtures and append operator-attested private fixtures."""

    fonts = {role: _find_fonts(role) for role in FONT_ENV}
    entries: list[dict[str, object]] = []
    records: list[FixtureRecord] = []
    for fixture in PUBLIC_FIXTURES:
        font_path = _font_for(fonts, fixture)
        image = output_dir / "fixtures" / f"{fixture.fixture_id}.png"
        settings = _render(fixture, font_path, image)
        entry: dict[str, object] = {
            "fixture_id": fixture.fixture_id,
            "language": fixture.language,
            "ground_truth": "\n".join(fixture.lines),
            "expected_lines": list(fixture.lines),
            "image_path": image.relative_to(output_dir).as_posix(),
            "image_sha256": sha256_file(image),
            "tags": list(fixture.tags),
            "source_kind": "archiv-authored-synthetic",
            "generation_method": {
                "renderer": "Pillow",
                "layout": fixture.layout,
                "direction": fixture.direction,
                "language_tag": fixture.language_tag,
                "transformation": settings,
            },
            "font": {
                "role": fixture.font_role,
                "path": str(font_path),
                "sha256": sha256_file(font_path),
            },
            "private": False,
        }
        entries.append(entry)
        records.append(_record(entry, output_dir, False))

    if private_corpus is not None:
        private_root = private_corpus.expanduser().resolve()
        manifest_path = private_root / "manifest.json"
        if not manifest_path.is_file():
            raise OcrBenchmarkError(f"private corpus manifest not found: {manifest_path}")
        payload_value: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload_value, dict) or payload_value.get("schema_version") != "1":
            raise OcrBenchmarkError("private corpus manifest schema_version must be '1'")
        payload = cast(dict[str, object], payload_value)
        fixtures_value = payload.get("fixtures")
        if not isinstance(fixtures_value, list) or len(fixtures_value) > MAX_PRIVATE_FIXTURES:
            raise OcrBenchmarkError(
                f"private corpus fixtures must be a list of at most {MAX_PRIVATE_FIXTURES}"
            )
        for value in fixtures_value:
            if not isinstance(value, dict):
                raise OcrBenchmarkError("private corpus fixture must be an object")
            entry = cast(dict[str, object], value)
            record = _record(entry, private_root, True)
            if any(item.fixture_id == record.fixture_id for item in records):
                raise OcrBenchmarkError(f"duplicate fixture_id: {record.fixture_id}")
            records.append(record)
            entries.append({**entry, "image_sha256": record.image_sha256, "private": True})

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "corpus": "archiv-multiengine-ocr",
        "licensing": (
            "Public phrases are Archiv-authored; local fonts are hashed but not copied."
        ),
        "private_corpus_included": any(record.private for record in records),
        "fixtures": entries,
    }
    path = output_dir / "corpus.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest["manifest_sha256"] = sha256_file(path)
    manifest["records"] = records
    return manifest


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


def _language_inventory(executable: str) -> tuple[Path | None, list[str]]:
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


def _tesseract_runner(candidate: str) -> CandidateRunner:
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
            directory, available = _language_inventory(executable)
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
                        path.stat().st_size
                        + (model_bytes if isinstance(model_bytes, int) else 0)
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


def _rapidocr_runner(
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
        if not isinstance(payload_value, dict) or payload_value.get("status") != "succeeded":
            raise OcrBenchmarkError("RapidOCR adapter response is invalid")
        payload = cast(dict[str, object], payload_value)
        values = payload.get("results")
        if not isinstance(values, list):
            raise OcrBenchmarkError("RapidOCR adapter results are invalid")
        outputs: dict[str, EngineText] = {}
        per_fixture = elapsed / max(1, len(values))
        for value in values:
            if not isinstance(value, dict):
                raise OcrBenchmarkError("RapidOCR result is invalid")
            item = cast(dict[str, object], value)
            fixture_id = item.get("fixture_id")
            text = item.get("text")
            lines = item.get("lines")
            if not isinstance(fixture_id, str) or not isinstance(text, str):
                raise OcrBenchmarkError("RapidOCR text result is invalid")
            if not isinstance(lines, list) or not all(
                isinstance(line, str) for line in lines
            ):
                raise OcrBenchmarkError("RapidOCR line result is invalid")
            outputs[fixture_id] = EngineText(
                text,
                tuple(cast(list[str], lines)),
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


def _blocked_kraken_runner(
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
        if item.get("status") == "succeeded"
        and isinstance(item.get("aggregate"), dict)
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
    ranks = {
        str(item["candidate_id"]): index + 1
        for index, item in enumerate(successful)
    }
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
            "best_measured_candidate": (
                options[0]["candidate_id"] if options else None
            ),
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
                try:
                    _, available = _language_inventory(executable)
                except (OSError, subprocess.SubprocessError, OcrBenchmarkError):
                    pass
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
                (f"tesseract:{candidate}", _tesseract_runner(candidate))
                for candidate in selected
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
        lines = Path("/proc/cpuinfo").read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
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
            "environment recorded; operator must confirm it is the target HP Victus "
            "Fedora machine"
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
