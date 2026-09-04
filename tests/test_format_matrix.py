"""Verify the committed open-format compatibility matrix against real runs.

Issue #37 requires a machine-readable, tested matrix.  This suite loads the
committed matrix, proves it covers exactly the real ingestion surface, and
re-runs every claimed family through the actual ingestion-normalization-
indexing pipeline so no claim can drift from behavior silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from format_matrix_support import (
    MARKER,
    build_blank_pdf,
    build_bmp,
    build_doc,
    build_docx,
    build_gif,
    build_inpage300,
    build_jpeg,
    build_odb,
    build_odf_flat,
    build_odf_formula,
    build_odf_package,
    build_pdf,
    build_png,
    build_ppt,
    build_pptx,
    build_rtf,
    build_svg,
    build_tar,
    build_text,
    build_tiff,
    build_wav,
    build_webp,
    build_xls,
    build_xlsx,
    build_zip,
)

from archiv.contracts import IngestionResult
from archiv.format_matrix import (
    FormatMatrix,
    coverage_problems,
    load_format_matrix,
)
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.formats import UnsupportedFormatError, suffix_for
from archiv.ingestion.normalizers import normalize
from archiv.search import rebuild_search_index, search_documents
from archiv.search.service import validate_citation

REPO_ROOT = Path(__file__).parents[1]
MATRIX_PATH = REPO_ROOT / "docs" / "format-compatibility.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "format-compatibility-matrix.schema.json"

_PACKAGE_SUFFIXES = {
    "odt",
    "ott",
    "odm",
    "otm",
    "ods",
    "ots",
    "odp",
    "otp",
    "odg",
    "otg",
}
_FLAT_SUFFIXES = {"fodt", "fods", "fodp", "fodg"}


def _write_fixture(directory: Path, suffix: str) -> Path:
    path = directory / f"matrix-probe{suffix}"
    bare = suffix.lstrip(".")
    if bare in _PACKAGE_SUFFIXES:
        build_odf_package(path, bare)
    elif bare in _FLAT_SUFFIXES:
        build_odf_flat(path, bare)
    elif bare == "odf":
        build_odf_formula(path)
    elif bare == "odb":
        build_odb(path)
    elif bare == "inp":
        build_inpage300(path)
    elif bare in {"txt", "md"}:
        path.write_bytes(build_text())
    elif bare == "pdf":
        path.write_bytes(build_pdf())
    elif bare == "doc":
        path.write_bytes(build_doc())
    elif bare == "rtf":
        path.write_bytes(build_rtf())
    elif bare == "docx":
        path.write_bytes(build_docx())
    elif bare == "xls":
        path.write_bytes(build_xls())
    elif bare == "xlsx":
        path.write_bytes(build_xlsx())
    elif bare == "ppt":
        path.write_bytes(build_ppt())
    elif bare == "pptx":
        path.write_bytes(build_pptx())
    elif bare == "png":
        path.write_bytes(build_png())
    elif bare in {"jpg", "jpeg"}:
        path.write_bytes(build_jpeg())
    elif bare == "gif":
        path.write_bytes(build_gif())
    elif bare == "bmp":
        path.write_bytes(build_bmp())
    elif bare in {"tiff", "tif"}:
        path.write_bytes(build_tiff())
    elif bare == "webp":
        path.write_bytes(build_webp())
    elif bare == "svg":
        path.write_bytes(build_svg())
    elif bare == "wav":
        path.write_bytes(build_wav())
    elif bare == "zip":
        path.write_bytes(build_zip())
    elif bare == "tar":
        path.write_bytes(build_tar())
    elif bare in {"tar.gz", "tgz"}:
        path.write_bytes(build_tar("gz"))
    elif bare in {"tar.bz2", "tbz2"}:
        path.write_bytes(build_tar("bz2"))
    elif bare in {"tar.xz", "txz"}:
        path.write_bytes(build_tar("xz"))
    else:
        raise AssertionError(f"no fixture builder for {suffix}")
    return path


@pytest.fixture(scope="module")
def matrix() -> FormatMatrix:
    return load_format_matrix(MATRIX_PATH)


def test_matrix_loads_and_matches_committed_schema(matrix: FormatMatrix) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    generated = matrix.model_json_schema(mode="serialization")
    assert schema == generated, (
        "format matrix schema drifted; regenerate schemas/format-compatibility-matrix.schema.json"
    )


def test_matrix_covers_exactly_the_supported_surface(matrix: FormatMatrix) -> None:
    problems = coverage_problems(matrix)
    assert problems == [], "; ".join(problems)


def test_rejected_examples_fail_closed(matrix: FormatMatrix) -> None:
    assert matrix.rejected_examples, "matrix must name tested rejections"
    for rejected in matrix.rejected_examples:
        with pytest.raises(UnsupportedFormatError):
            suffix_for(f"probe{rejected.suffix}")


def test_output_claims_are_deliberate(matrix: FormatMatrix) -> None:
    outputs = {output.format for output in matrix.outputs}
    assert {"docx", "pdf", "odf"} <= outputs
    generated = {output.format for output in matrix.outputs if output.generated}
    assert generated == {"docx", "pdf"}


def _ingest(tmp_path: Path, suffix: str) -> tuple[IngestionResult, list[dict[str, object]], Path]:
    home = tmp_path / "home"
    source = _write_fixture(tmp_path, suffix)
    digest = sha256_file(source)
    result = ingest_file(source, home=home)
    assert result.object_sha256 == digest
    assert result.source_hash_unchanged
    document = normalize(source, result.object_sha256)
    segments = [dict(segment.locator) for segment in document.segments]
    return result, segments, home


@pytest.mark.parametrize(
    "suffix",
    [
        ".txt",
        ".md",
        ".pdf",
        ".doc",
        ".rtf",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".ott",
        ".odm",
        ".otm",
        ".fodt",
        ".ods",
        ".ots",
        ".fods",
        ".odp",
        ".otp",
        ".fodp",
        ".odg",
        ".otg",
        ".fodg",
        ".odf",
        ".odb",
        ".inp",
        ".svg",
        ".zip",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tbz2",
        ".tar.xz",
        ".txz",
    ],
)
def test_family_claims_match_live_ingestion(
    tmp_path: Path, matrix: FormatMatrix, suffix: str
) -> None:
    family = matrix.family_for_suffix(suffix)
    result, segments, home = _ingest(tmp_path, suffix)
    assert result.media_type in family.media_types
    assert family.immutable_ingestion

    claimed_shapes = {frozenset(shape) for shape in family.locator_shapes}
    if family.text_extraction == "metadata_only" and suffix == ".odb":
        assert segments, "ODB object names must be surfaced"
    else:
        assert segments, f"{suffix} must produce text segments per the matrix"
    for locator in segments:
        assert frozenset(locator) in claimed_shapes, (
            f"{suffix} produced unclaimed locator keys {sorted(locator)}"
        )
    for segment in segments:
        assert not frozenset(segment) - frozenset().union(*claimed_shapes)

    if family.grounding:
        seeds = {".odb": "ArchivMatrixEvidence", ".odf": "x+1"}
        seed = seeds.get(suffix, MARKER)
        build = rebuild_search_index(home=home)
        assert build.object_count >= 1
        matches = search_documents(seed, home=home)
        assert matches, f"{suffix}: matrix claims grounding but nothing is retrievable"
        for match in matches:
            validation = validate_citation(match.citation, home=home)
            assert validation.valid, f"{suffix}: citation failed validation"


def test_image_family_is_metadata_only_without_local_ocr(
    tmp_path: Path, matrix: FormatMatrix
) -> None:
    for suffix in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"):
        family = matrix.family_for_suffix(suffix)
        directory = tmp_path / suffix.lstrip(".")
        directory.mkdir(parents=True)
        result, segments, _home = _ingest(directory, suffix)
        assert result.media_type in family.media_types
        assert family.text_extraction == "visual_ocr_conditional"
        assert segments == [], "OCR must stay disabled in this environment"
        assert any("without local Tesseract" in note for note in family.known_limits)


def test_audio_family_records_metadata_only(tmp_path: Path, matrix: FormatMatrix) -> None:
    family = matrix.family_for_suffix(".wav")
    result, segments, _home = _ingest(tmp_path, ".wav")
    assert result.media_type == "audio/wav"
    assert family.text_extraction == "metadata_only"
    assert segments == []
    assert not family.grounding


def test_textless_pdf_pages_stay_valid_without_ocr(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "blank.pdf"
    source.write_bytes(build_blank_pdf())
    result = ingest_file(source, home=home)
    document = normalize(source, result.object_sha256)
    assert all(not segment.text.strip() for segment in document.segments)
    assert result.source_hash_unchanged
