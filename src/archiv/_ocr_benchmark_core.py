"""Core types, lawful fixtures, and OCR comparison metrics."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

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
    return tuple(normalized for line in text.splitlines() if (normalized := normalize_text(line)))


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
    reference_lines = tuple(normalize_text(line) for line in (expected_lines or (reference,)))
    hypothesis_lines = tuple(
        normalize_text(line) for line in (observed_lines or normalize_lines(hypothesis))
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
