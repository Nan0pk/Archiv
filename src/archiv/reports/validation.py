"""Independent DOCX package, citation, and rendered-page validation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from zipfile import BadZipFile, ZipFile, is_zipfile

from docx import Document as open_document
from docx.opc.exceptions import PackageNotFoundError
from PIL import Image
from pypdf import PdfReader

from archiv.contracts import FileEvidence
from archiv.hashing import file_evidence, sha256_bytes, sha256_file
from archiv.report_contracts import (
    REQUIRED_REPORT_SECTIONS,
    ReportManifest,
    ReportValidation,
)
from archiv.reports.formatting import format_locator
from archiv.search import read_source_excerpt, validate_citation
from archiv.storage.layout import ArchivLayout

REQUIRED_MEMBERS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/styles.xml",
}


def _document_content(path: Path) -> tuple[str, list[str]]:
    document = open_document(str(path))
    paragraph_texts = [paragraph.text for paragraph in document.paragraphs]
    blocks = list(paragraph_texts)
    for table in document.tables:
        for row in table.rows:
            blocks.extend(cell.text for cell in row.cells)
    return "\n".join(blocks), paragraph_texts


def _render_docx(path: Path, output_dir: Path) -> Path:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable is None:
        raise RuntimeError("LibreOffice is required for report rendering")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{path.stem}.pdf"
    pdf_path.unlink(missing_ok=True)
    with TemporaryDirectory(prefix="archiv-lo-") as profile_dir:
        profile = Path(profile_dir) / "profile"
        home = Path(profile_dir) / "home"
        profile.mkdir()
        home.mkdir()
        environment = os.environ.copy()
        environment["HOME"] = str(home)
        completed = subprocess.run(
            [
                executable,
                "--headless",
                f"-env:UserInstallation=file://{profile.as_posix()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env=environment,
        )
    if completed.returncode != 0 or not pdf_path.is_file():
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"LibreOffice render failed: {detail or completed.returncode}")
    return pdf_path


def _render_pages(pdf_path: Path, output_dir: Path) -> list[Path]:
    executable = shutil.which("pdftoppm")
    if executable is None:
        raise RuntimeError("pdftoppm is required for page-image validation")
    for stale in output_dir.glob("page-*.png"):
        stale.unlink()
    prefix = output_dir / "page"
    completed = subprocess.run(
        [executable, "-png", "-r", "144", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"PDF page rendering failed: {completed.stderr.strip()}")
    pages = sorted(output_dir.glob("page-*.png"))
    if not pages:
        raise RuntimeError("PDF page rendering produced no images")
    return pages


def _validate_source_numbers(manifest: ReportManifest, errors: list[str]) -> None:
    numbers = [source.number for source in manifest.sources]
    expected = list(range(1, len(manifest.sources) + 1))
    if numbers != expected:
        errors.append("manifest source numbers must be contiguous and ordered from 1")
    segment_ids = [source.citation.segment_id for source in manifest.sources]
    if len(segment_ids) != len(set(segment_ids)):
        errors.append("manifest contains duplicate citation segments")


def validate_report(
    docx_path: Path,
    manifest_path: Path,
    *,
    home: Path | None = None,
    render: bool = False,
    evidence_dir: Path | None = None,
) -> ReportValidation:
    """Validate package structure, citations, source integrity, and optional rendering."""

    errors: list[str] = []
    warnings: list[str] = []
    page_images: list[FileEvidence] = []
    docx_sha256: str | None = None
    pdf_path_value: str | None = None
    pdf_sha256: str | None = None
    extracted_text_sha256: str | None = None
    page_count = 0

    if not docx_path.is_file():
        return ReportValidation(valid=False, errors=["required DOCX output is missing"])
    docx_sha256 = sha256_file(docx_path)
    if not manifest_path.is_file():
        return ReportValidation(
            valid=False,
            errors=["report manifest is missing"],
            docx_sha256=docx_sha256,
        )

    try:
        manifest = ReportManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return ReportValidation(
            valid=False,
            errors=[f"manifest is invalid: {type(error).__name__}: {error}"],
            docx_sha256=docx_sha256,
        )

    if manifest.required_sections != list(REQUIRED_REPORT_SECTIONS):
        errors.append("manifest required sections do not match the report contract")
    _validate_source_numbers(manifest, errors)
    if manifest.docx_sha256 != docx_sha256:
        errors.append("DOCX hash does not match the manifest")
    if Path(manifest.docx_path).resolve() != docx_path.resolve():
        errors.append("manifest DOCX path does not match the validated output")

    if not is_zipfile(docx_path):
        errors.append("DOCX is not a ZIP package")
    else:
        try:
            with ZipFile(docx_path) as archive:
                missing = sorted(REQUIRED_MEMBERS - set(archive.namelist()))
                if missing:
                    errors.append("DOCX package members missing: " + ", ".join(missing))
                if archive.testzip() is not None:
                    errors.append("DOCX package contains a corrupt member")
        except BadZipFile as error:
            errors.append(f"DOCX package is corrupt: {error}")

    try:
        text, paragraph_texts = _document_content(docx_path)
    except (OSError, ValueError, BadZipFile, PackageNotFoundError) as error:
        errors.append(f"DOCX content cannot be read: {type(error).__name__}: {error}")
        text = ""
        paragraph_texts = []

    for section in REQUIRED_REPORT_SECTIONS:
        if section not in text:
            errors.append(f"required section missing: {section}")

    layout = ArchivLayout.resolve(home)
    for source in manifest.sources:
        validation = validate_citation(source.citation, home=home)
        if not validation.valid:
            errors.append(f"citation {source.number} is invalid: " + "; ".join(validation.errors))
            continue
        excerpt = read_source_excerpt(source.citation, home=home)
        if excerpt != source.excerpt:
            errors.append(f"citation {source.number} excerpt does not match source evidence")
        marker = f"[{source.number}]"
        if not any(
            source.excerpt in paragraph and marker in paragraph for paragraph in paragraph_texts
        ):
            errors.append(f"inline citation {marker} missing from its finding")
        if source.citation.segment_id not in text:
            errors.append(f"citation {source.number} segment identifier missing from appendix")
        if source.citation.source_name not in text:
            errors.append(f"citation {source.number} source name missing from appendix")
        expected_locator = format_locator(source.citation.locator)
        if expected_locator not in text:
            errors.append(f"citation {source.number} locator missing from appendix")
        original = layout.original_path(source.citation.object_sha256)
        if not original.is_file() or sha256_file(original) != source.citation.object_sha256:
            errors.append(f"citation {source.number} canonical source changed or is missing")

    if render and not errors:
        destination = evidence_dir or docx_path.with_suffix("")
        try:
            pdf_path = _render_docx(docx_path, destination)
            pdf_path_value = str(pdf_path)
            pdf_sha256 = sha256_file(pdf_path)
            reader = PdfReader(str(pdf_path))
            page_count = len(reader.pages)
            extracted_text = "\n".join((page.extract_text() or "") for page in reader.pages)
            extracted_text_sha256 = sha256_bytes(extracted_text.encode("utf-8"))
            if page_count < 1:
                errors.append("rendered PDF has no pages")
            for section in REQUIRED_REPORT_SECTIONS:
                if section not in extracted_text:
                    errors.append(f"rendered PDF is missing required section: {section}")
            for source in manifest.sources:
                if f"[{source.number}]" not in extracted_text:
                    errors.append(f"rendered PDF is missing citation [{source.number}]")
            page_paths = _render_pages(pdf_path, destination)
            if len(page_paths) != page_count:
                errors.append("rendered page-image count does not match PDF page count")
            for page_path in page_paths:
                with Image.open(page_path) as image:
                    grayscale = image.convert("L")
                    minimum, maximum = cast(tuple[int, int], grayscale.getextrema())
                    if image.width < 100 or image.height < 100:
                        errors.append(f"rendered page is unexpectedly small: {page_path.name}")
                    if minimum >= 250 and maximum >= 250:
                        errors.append(f"rendered page appears blank: {page_path.name}")
                page_images.append(file_evidence(page_path))
        except (OSError, RuntimeError, ValueError) as error:
            errors.append(f"render validation failed: {type(error).__name__}: {error}")

    return ReportValidation(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        docx_sha256=docx_sha256,
        pdf_path=pdf_path_value,
        pdf_sha256=pdf_sha256,
        extracted_text_sha256=extracted_text_sha256,
        page_count=page_count,
        page_images=page_images,
    )


def write_validation(path: Path, validation: ReportValidation) -> None:
    path.write_text(
        json.dumps(validation.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
