"""Shared contracts, validation, hashing, and deterministic archive helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

SCHEMA_VERSION = "1"
DEFAULT_BENCHMARK = Path(__file__).resolve().parents[2] / "benchmarks/field_trial/benchmark.json"
PRIVATE_ROOT = Path(".archiv-field-trial/private")
SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)
FIXED_DATETIME = datetime(2026, 1, 1, tzinfo=UTC)
PRIVATE_KEYS = {
    "content",
    "excerpt",
    "filename",
    "path",
    "prompt",
    "question",
    "raw_model_response",
    "source_name",
    "text",
}


class BenchmarkError(ValueError):
    """The committed benchmark definition is malformed or unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_zip(raw: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(raw)) as source, ZipFile(output, "w") as target:
        for name in sorted(source.namelist()):
            info = ZipInfo(name, FIXED_TIME)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            content = source.read(name)
            if name == "docProps/core.xml":
                content = re.sub(
                    rb"<dcterms:(created|modified)([^>]*)>[^<]+</dcterms:\1>",
                    lambda match: (
                        b"<dcterms:"
                        + match.group(1)
                        + match.group(2)
                        + b">2026-01-01T00:00:00Z</dcterms:"
                        + match.group(1)
                        + b">"
                    ),
                    content,
                )
            target.writestr(info, content)
    return output.getvalue()


def load_benchmark(path: Path = DEFAULT_BENCHMARK) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError(f"cannot read benchmark: {error}") from error
    if not isinstance(value, dict):
        raise BenchmarkError("benchmark must be a schema_version 1 object")
    value = cast(dict[str, Any], value)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkError("benchmark must be a schema_version 1 object")
    corpus = value.get("corpus")
    questions = value.get("questions")
    if not isinstance(corpus, list) or not corpus:
        raise BenchmarkError("benchmark corpus must be non-empty")
    if not isinstance(questions, list):
        raise BenchmarkError("benchmark must contain at least 20 questions")
    corpus = cast(list[Any], corpus)
    questions = cast(list[Any], questions)
    if len(questions) < 20:
        raise BenchmarkError("benchmark must contain at least 20 questions")
    source_ids: set[str] = set()
    filenames: set[str] = set()
    for raw_source in corpus:
        if not isinstance(raw_source, dict):
            raise BenchmarkError("corpus entries must be objects")
        source = cast(dict[str, Any], raw_source)
        source_id = source.get("id")
        filename = source.get("filename")
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise BenchmarkError(f"invalid or duplicate corpus id: {source_id}")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or filename in filenames
        ):
            raise BenchmarkError(f"unsafe or duplicate corpus filename: {filename}")
        source_ids.add(source_id)
        filenames.add(filename)
    question_ids: set[str] = set()
    for raw_question in questions:
        if not isinstance(raw_question, dict):
            raise BenchmarkError("questions must be objects")
        question = cast(dict[str, Any], raw_question)
        question_id = question.get("id")
        text = question.get("question")
        expected = question.get("expected_sources")
        facts = question.get("required_facts")
        if not isinstance(question_id, str) or not question_id or question_id in question_ids:
            raise BenchmarkError(f"invalid or duplicate question id: {question_id}")
        if not isinstance(text, str) or not text.strip():
            raise BenchmarkError(f"question {question_id} has no text")
        if not isinstance(expected, list):
            raise BenchmarkError(f"question {question_id} has invalid expected_sources")
        expected = cast(list[Any], expected)
        if not all(isinstance(item, str) for item in expected):
            raise BenchmarkError(f"question {question_id} has invalid expected_sources")
        unknown = set(expected) - source_ids
        if unknown:
            raise BenchmarkError(f"question {question_id} references unknown sources: {unknown}")
        if not isinstance(facts, list):
            raise BenchmarkError(f"question {question_id} has malformed required_facts")
        facts = cast(list[Any], facts)
        if any(
            not isinstance(fact, dict)
            or not isinstance(cast(dict[str, Any], fact).get("terms"), list)
            for fact in facts
        ):
            raise BenchmarkError(f"question {question_id} has malformed required_facts")
        question_ids.add(question_id)
    return cast(dict[str, object], value)
