"""Machine-readable open-format compatibility matrix contract (issue #37).

The matrix states, for every supported ingestion format, exactly which
behaviors Archiv verifies: detection, immutable ingestion, extraction depth,
locator quality, grounded retrieval, rendering/export posture, and known
limits.  The committed matrix data lives at ``docs/format-compatibility.json``
and is re-verified by the test-suite against live ingestion runs, so the
claims cannot drift from the implementation silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from archiv import __version__
from archiv.contracts import StrictModel
from archiv.ingestion.formats import MEDIA_TYPES, SUPPORTED_SUFFIXES, media_type_for

MATRIX_SCHEMA_VERSION = "1"


class FormatFamily(StrictModel):
    """Verified behavior for one family of related formats."""

    schema_version: str = "1"
    family: str = Field(min_length=1)
    suffixes: tuple[str, ...] = Field(min_length=1)
    media_types: tuple[str, ...] = Field(min_length=1)
    detection: str = Field(min_length=1)
    immutable_ingestion: bool
    text_extraction: Literal["native", "visual_ocr_conditional", "metadata_only"]
    structure: tuple[str, ...] = ()
    locator_shapes: tuple[tuple[str, ...], ...] = ()
    grounding: bool
    preview_render: str = Field(min_length=1)
    macros: str = Field(min_length=1)
    encryption: str = Field(min_length=1)
    known_limits: tuple[str, ...] = ()


class RejectedFormat(StrictModel):
    """One explicitly tested rejection case."""

    schema_version: str = "1"
    suffix: str = Field(min_length=1)
    example: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class OutputFormat(StrictModel):
    """One document format Archiv itself produces through the report workflow."""

    schema_version: str = "1"
    format: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    generated: bool
    verification: str = Field(min_length=1)
    notes: str = ""


class FormatMatrix(StrictModel):
    """Versioned, tested compatibility claims for the ingestion surface."""

    schema_version: str = MATRIX_SCHEMA_VERSION
    product_version: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    families: tuple[FormatFamily, ...] = Field(min_length=1)
    outputs: tuple[OutputFormat, ...] = ()
    rejected_examples: tuple[RejectedFormat, ...] = ()

    def family_for_suffix(self, suffix: str) -> FormatFamily:
        for family in self.families:
            if suffix in family.suffixes:
                return family
        raise KeyError(f"no matrix family claims suffix {suffix}")


def matrix_path() -> Path:
    """Locate the committed compatibility matrix.

    The matrix is authored at ``docs/format-compatibility.json`` and shipped into
    the wheel as package data, so both a repository checkout and an installed
    Archiv resolve the same claims.  Installed package data is preferred because
    it is the copy that matches the running code.
    """

    packaged = Path(__file__).resolve().parent / "data" / "format-compatibility.json"
    if packaged.is_file():
        return packaged
    checkout = Path(__file__).resolve().parents[2] / "docs" / "format-compatibility.json"
    if checkout.is_file():
        return checkout
    raise ValueError(
        "the format compatibility matrix is not available in this installation; "
        "expected packaged data at archiv/data/format-compatibility.json"
    )


def load_format_matrix(path: Path) -> FormatMatrix:
    """Load and validate the committed compatibility matrix."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"format matrix could not be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"format matrix is not valid JSON: {error}") from error
    return FormatMatrix.model_validate(payload)


def coverage_problems(matrix: FormatMatrix) -> list[str]:
    """Structural disagreements between the matrix and the real code surface."""

    problems: list[str] = []
    claimed: list[str] = []
    for family in matrix.families:
        for suffix in family.suffixes:
            if not suffix.startswith("."):
                problems.append(f"family {family.family!r} claims a bare suffix {suffix!r}")
            if suffix in claimed:
                problems.append(f"suffix {suffix} is claimed by more than one family")
            claimed.append(suffix)
    missing = sorted(SUPPORTED_SUFFIXES - set(claimed))
    extra = sorted(set(claimed) - SUPPORTED_SUFFIXES)
    for suffix in missing:
        problems.append(f"supported suffix {suffix} has no matrix entry")
    for suffix in extra:
        problems.append(f"matrix claims unsupported suffix {suffix}")
    if matrix.product_version != __version__:
        problems.append(
            f"matrix product version {matrix.product_version} does not match "
            f"the installed Archiv {__version__}"
        )
    for family in matrix.families:
        for suffix in family.suffixes:
            if suffix in MEDIA_TYPES and suffix in SUPPORTED_SUFFIXES:
                resolved = media_type_for(f"matrix-probe{suffix}")
                if resolved not in family.media_types:
                    problems.append(
                        f"family {family.family!r} omits the registered media type "
                        f"{resolved} for {suffix}"
                    )
    for family in matrix.families:
        if (
            family.text_extraction == "metadata_only"
            and family.grounding
            and not family.locator_shapes
        ):
            problems.append(
                f"family {family.family!r} claims grounded metadata without locator shapes"
            )
    return problems
