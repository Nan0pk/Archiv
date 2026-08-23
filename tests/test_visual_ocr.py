from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from archiv.contracts import NormalizedDocument
from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index, search_documents, validate_citation


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _install_fake_tesseract(
    bin_dir: Path,
    *,
    marker: str,
    languages: tuple[str, ...] = ("eng", "urd", "ara"),
) -> None:
    payload = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
        "left\ttop\twidth\theight\tconf\ttext\n"
        f"5\t1\t1\t1\t1\t1\t20\t20\t300\t24\t96.5\t{marker}\n"
    )
    language_output = "List of available languages ({count}):\n{items}\n".format(
        count=len(languages),
        items="\n".join(languages),
    )
    _write_executable(
        bin_dir / "tesseract",
        f"""#!{sys.executable}
import sys

if "--version" in sys.argv:
    print("tesseract 5.5.0-test")
elif "--list-langs" in sys.argv:
    sys.stdout.write({language_output!r})
else:
    sys.stdout.write({payload!r})
""",
    )


def _install_fake_pdftoppm(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "pdftoppm",
        f"""#!{sys.executable}
from pathlib import Path
import sys
from PIL import Image

if "-v" in sys.argv:
    print("pdftoppm version 26.1-test", file=sys.stderr)
else:
    prefix = Path(sys.argv[-1])
    Image.new("RGB", (640, 160), "white").save(prefix.with_suffix(".png"))
""",
    )


def _enable_fake_ocr(
    monkeypatch: pytest.MonkeyPatch,
    bin_dir: Path,
    *,
    languages: str = "eng+urd+ara",
) -> None:
    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv("ARCHIV_OCR", "auto")
    monkeypatch.setenv("ARCHIV_OCR_SANDBOX", "off")
    monkeypatch.setenv("ARCHIV_OCR_LANGUAGES", languages)


def test_image_ocr_is_searchable_and_citable(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = "ARCHIV-IMAGE-MARKER-2026"
    _install_fake_tesseract(bin_dir, marker=marker)
    _enable_fake_ocr(monkeypatch, bin_dir)

    home = tmp_path / "archiv-home"
    result = ingest_file(ingestion_corpus / "scanned-page.png", home=home)
    derived = Path(result.derived_root)
    manifest = json.loads((derived / "ocr" / "status.json").read_text(encoding="utf-8"))
    normalized = NormalizedDocument.model_validate_json(
        (derived / "normalized" / "document.json").read_text(encoding="utf-8")
    )

    assert manifest["status"] == "succeeded"
    assert manifest["engine"] == "tesseract"
    assert manifest["languages"] == ["eng", "urd", "ara"]
    assert manifest["pages"][0]["image_origin"] == "canonical_original"
    assert manifest["pages"][0]["raw_output_path"] == "ocr/page-0001.tsv"
    assert (derived / "ocr" / "page-0001.tsv").is_file()
    assert any(
        segment.text == marker and segment.locator["origin"] == "visual_ocr"
        for segment in normalized.segments
    )
    assert any(
        item.processor == "archiv.visual-ocr" and item.status == "succeeded"
        for item in result.processing
    )

    rebuild_search_index(home=home)
    matches = search_documents(marker, home=home)
    assert len(matches) == 1
    citation = matches[0].citation
    assert citation.source_name == "scanned-page.png"
    assert citation.locator["origin"] == "visual_ocr"
    assert citation.locator["page"] == 1
    assert citation.locator["region"] == {
        "x": 20,
        "y": 20,
        "width": 300,
        "height": 24,
        "unit": "pixel",
    }
    assert validate_citation(citation, home=home).valid is True


def test_image_only_pdf_pages_are_rendered_then_ocrd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = "ARCHIV-SCANNED-PDF-MARKER-2026"
    _install_fake_tesseract(bin_dir, marker=marker)
    _install_fake_pdftoppm(bin_dir)
    _enable_fake_ocr(monkeypatch, bin_dir)

    source = tmp_path / "image-only.pdf"
    canvas = Canvas(str(source), pagesize=(320, 200), invariant=1)
    canvas.rect(20, 20, 280, 160)
    canvas.showPage()
    canvas.save()

    home = tmp_path / "archiv-home"
    result = ingest_file(source, home=home)
    derived = Path(result.derived_root)
    manifest = json.loads((derived / "ocr" / "status.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "succeeded"
    assert manifest["renderer"] == "pdftoppm"
    assert manifest["pages_requiring_ocr"] == [1]
    assert manifest["pages"][0]["image_origin"] == "rendered_pdf_page"
    rendered = derived / "previews" / "pages" / "page-0001.png"
    assert rendered.is_file()
    assert manifest["pages"][0]["image_sha256"]

    rebuild_search_index(home=home)
    matches = search_documents(marker, home=home)
    assert len(matches) == 1
    assert matches[0].citation.source_name == "image-only.pdf"
    assert matches[0].citation.locator["origin"] == "visual_ocr"
    assert matches[0].citation.locator["page"] == 1


def test_bubblewrap_sandbox_can_read_original_under_tmp_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: bwrap's --tmpfs /tmp must not mask an ARCHIV_HOME under /tmp."""

    if shutil.which("bwrap") is None or shutil.which("tesseract") is None:
        pytest.skip("bubblewrap or tesseract not installed")

    monkeypatch.setenv("ARCHIV_OCR", "auto")
    monkeypatch.delenv("ARCHIV_OCR_SANDBOX", raising=False)
    monkeypatch.setenv("ARCHIV_OCR_LANGUAGES", "eng")

    home = tmp_path / "archiv-home"
    source = tmp_path / "scanned-page.png"
    Image.new("RGB", (200, 80), "white").save(source)

    result = ingest_file(source, home=home)
    derived = Path(result.derived_root)
    manifest = json.loads((derived / "ocr" / "status.json").read_text(encoding="utf-8"))

    assert manifest["engine_sandbox"] == "bubblewrap"
    assert manifest["status"] == "succeeded", manifest.get("reason")
    assert manifest["pages"][0]["status"] == "succeeded", manifest["pages"]


def test_missing_requested_language_skips_without_fabricating_text(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_tesseract(
        bin_dir,
        marker="SHOULD-NOT-BE-EMITTED",
        languages=("eng",),
    )
    _enable_fake_ocr(monkeypatch, bin_dir, languages="urd")

    home = tmp_path / "archiv-home"
    result = ingest_file(ingestion_corpus / "scanned-page.png", home=home)
    derived = Path(result.derived_root)
    manifest = json.loads((derived / "ocr" / "status.json").read_text(encoding="utf-8"))
    normalized = NormalizedDocument.model_validate_json(
        (derived / "normalized" / "document.json").read_text(encoding="utf-8")
    )

    assert manifest["status"] == "skipped"
    assert manifest["missing_languages"] == ["urd"]
    assert normalized.segments == []
    assert any(
        item.processor == "archiv.visual-ocr" and item.status == "skipped"
        for item in result.processing
    )
