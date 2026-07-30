"""Independent validation for the source-marker capability."""

from __future__ import annotations

from pathlib import Path

from archiv.contracts import ValidationReport
from archiv.hashing import file_evidence, sha256_bytes


def validate_source_marker(
    *,
    source_path: Path,
    output_path: Path,
    expected_marker: str,
    source_hash_before: str,
) -> ValidationReport:
    """Validate output bytes and prove that the source remained unchanged."""

    errors: list[str] = []
    actual_output = None

    if not source_path.is_file():
        errors.append("source_missing_after_execution")
    else:
        source_after = file_evidence(source_path, display_path="source.txt")
        if source_after.sha256 != source_hash_before:
            errors.append("source_hash_changed")

    expected_bytes = f"HARNESS_OK\n{expected_marker}\n".encode()
    expected_output_sha256 = sha256_bytes(expected_bytes)

    if not output_path.is_file():
        errors.append("required_output_missing")
    else:
        actual_output = file_evidence(output_path, display_path="outputs/probe.txt")
        if output_path.read_bytes() != expected_bytes:
            errors.append("output_bytes_mismatch")

    return ValidationReport(
        passed=not errors,
        errors=errors,
        expected_output_sha256=expected_output_sha256,
        actual_output=actual_output,
    )
