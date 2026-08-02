"""Private-output and ground-truth comparison helpers for InPage research."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import unicodedata
from dataclasses import asdict
from pathlib import Path

from archiv.research.inpage_types import (
    ExtractionError,
    NormalizationMode,
    QuranComparison,
    TextMetrics,
    text_sha256,
)


def compute_git_blob_sha1(data: bytes) -> str:
    """Compute Git's canonical blob identity for pinned-download validation."""

    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def write_private_text(path: Path, text: str) -> None:
    """Write private text only to a newly created mode-0600 path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def metrics_json(metrics: TextMetrics) -> str:
    """Serialize sanitized metrics deterministically."""

    return json.dumps(asdict(metrics), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_quran_text(text: str, mode: NormalizationMode) -> str:
    """Apply exactly one explicit Quran-comparison normalization."""

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if mode == "exact_nfc":
        return normalized
    if mode == "whitespace_normalized":
        return " ".join(normalized.split())
    if mode == "diacritic_insensitive":
        stripped = "".join(
            character
            for character in normalized
            if not (0x064B <= ord(character) <= 0x065F or ord(character) == 0x0670)
            and unicodedata.category(character) != "Mn"
        )
        return " ".join(stripped.split())
    if mode == "verse_symbol_normalized":
        verse_symbols = {0x06DD, 0xFD3E, 0xFD3F}
        return " ".join(
            " ".join("" if ord(ch) in verse_symbols else ch for ch in normalized).split()
        )
    raise ExtractionError(f"unsupported normalization mode: {mode}")


def compare_quran_text(
    extracted: str, reference: str, *, mode: NormalizationMode
) -> QuranComparison:
    """Compare private texts while returning hashes and counts only."""

    left = normalize_quran_text(extracted, mode)
    right = normalize_quran_text(reference, mode)
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    matching = sum(block.size for block in matcher.get_matching_blocks())
    return QuranComparison(
        mode=mode,
        extracted_sha256=text_sha256(left),
        reference_sha256=text_sha256(right),
        extracted_length=len(left),
        reference_length=len(right),
        matching_characters=matching,
        matching_ratio=matcher.ratio(),
        length_delta=len(left) - len(right),
        exact_match=left == right,
    )
