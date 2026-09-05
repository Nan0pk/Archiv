# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
"""Tests for Capability Expansion Milestone 4 (PDF as a First-Class Object).

Covers:
- Coordinates and layout-aware multi-column ordering.
- Table extraction into NormalizedTable and row-aware retrieval.
- Annotations, bookmarks / outlines, and form fields extraction.
- Embedded PDF attachments ingested through the containment table.
- Encryption handling (empty password unlock vs password-locked detection).
- Fast page rendering via pypdfium2.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import cast

from pypdf import PdfWriter
from pypdf.annotations import Text
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table
from reportlab.platypus.tables import TableStyle

from archiv.ingestion import ingest_file
from archiv.ingestion.normalize_documents import normalize_pdf
from archiv.ingestion.visual_ocr import _render_pdf_page
from archiv.search import rebuild_search_index, search_documents
from archiv.search.retrieval import _row_aware_phrase_matches
from archiv.storage.database import ArchivDatabase, get_containment_for_parent
from archiv.storage.layout import ArchivLayout


def _make_two_column_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 800))
    # Draw interleaved in content stream: left 1, right 1, left 2, right 2
    c.drawString(50, 700, "Left Column Alpha Line 1")
    c.drawString(350, 700, "Right Column Beta Line 1")
    c.drawString(50, 680, "Left Column Alpha Line 2")
    c.drawString(350, 680, "Right Column Beta Line 2")
    c.save()
    return buf.getvalue()


def _make_table_pdf() -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf)
    data = [
        ["ProductCode", "ItemPrice"],
        ["WidgetDeluxe", "$99"],
        ["GadgetPro", "$199"],
    ]
    t = Table(data)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([t])
    return buf.getvalue()


def _make_rich_metadata_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=500, height=500)
    # Add outline / bookmark
    writer.add_outline_item("Executive Summary", 0)
    # Add annotation comment
    annot = Text(rect=(50, 50, 150, 150), text="Reviewer Note: Verification passed")
    writer.add_annotation(0, annot)
    # Add embedded attachment
    writer.add_attachment("embedded_payload.txt", b"Embedded text content inside PDF.")

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_encrypted_pdf(password: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_pdf_coordinates_and_layout_aware_ordering(tmp_path: Path) -> None:
    pdf_bytes = _make_two_column_pdf()
    pdf_path = tmp_path / "two_column.pdf"
    pdf_path.write_bytes(pdf_bytes)

    doc = normalize_pdf(
        pdf_path, "0" * 64, source_name="two_column.pdf", media_type="application/pdf"
    )
    assert doc.kind == "pdf"
    assert len(doc.segments) >= 2

    # Check that segments have bounding box coordinates
    for seg in doc.segments:
        assert "page" in seg.locator
        assert "bbox" in seg.locator
        bbox = seg.locator["bbox"]
        assert isinstance(bbox, list)
        assert len(cast(list[object], bbox)) == 4

    texts = [seg.text for seg in doc.segments]

    # Column 1 lines should come before Column 2 lines (not interleaved)
    alpha1_idx = next(i for i, t in enumerate(texts) if "Left Column Alpha Line 1" in t)
    alpha2_idx = next(i for i, t in enumerate(texts) if "Left Column Alpha Line 2" in t)
    beta1_idx = next(i for i, t in enumerate(texts) if "Right Column Beta Line 1" in t)
    beta2_idx = next(i for i, t in enumerate(texts) if "Right Column Beta Line 2" in t)

    assert alpha1_idx < alpha2_idx
    assert alpha2_idx < beta1_idx
    assert beta1_idx < beta2_idx


def test_pdf_table_extraction_and_row_aware_retrieval(tmp_path: Path) -> None:
    pdf_bytes = _make_table_pdf()
    pdf_path = tmp_path / "table.pdf"
    pdf_path.write_bytes(pdf_bytes)

    home = tmp_path / "home"
    res = ingest_file(pdf_path, home=home)
    rebuild_search_index(home=home)

    # Verify NormalizedTable extraction
    layout = ArchivLayout.resolve(home)
    doc_path = layout.derived_root(res.object_sha256) / "normalized" / "document.json"
    assert doc_path.is_file()

    # Search for cell contents
    m1 = search_documents("WidgetDeluxe", home=home)
    assert len(m1) >= 1
    assert m1[0].citation.locator.get("row") is not None

    # Test row-aware phrase matching across adjacent cells: "WidgetDeluxe $99"
    phrase_matches = _row_aware_phrase_matches("WidgetDeluxe $99", home=home, limit=5)
    assert len(phrase_matches) >= 1
    assert phrase_matches[0].citation.object_sha256 == res.object_sha256


def test_pdf_annotations_bookmarks_and_attachments_containment(tmp_path: Path) -> None:
    pdf_bytes = _make_rich_metadata_pdf()
    pdf_path = tmp_path / "rich.pdf"
    pdf_path.write_bytes(pdf_bytes)

    home = tmp_path / "home"
    res = ingest_file(pdf_path, home=home)
    rebuild_search_index(home=home)

    # Check outline bookmark segment
    m_bm = search_documents("Executive Summary", home=home)
    assert len(m_bm) >= 1
    assert m_bm[0].citation.locator.get("origin") == "bookmark"

    # Check annotation comment segment
    m_annot = search_documents("Reviewer Note", home=home)
    assert len(m_annot) >= 1
    assert m_annot[0].citation.locator.get("origin") == "annotation"

    # Check embedded attachment containment
    database = ArchivDatabase(ArchivLayout.resolve(home).database)
    containment_records = get_containment_for_parent(database, res.object_sha256)
    assert len(containment_records) >= 1
    rec = containment_records[0]
    assert rec["internal_path"] == "embedded_payload.txt"

    # Verify attachment content is searchable
    m_att = search_documents("Embedded text content", home=home)
    assert len(m_att) >= 1
    assert m_att[0].citation.object_sha256 == rec["child_sha256"]


def test_pdf_encryption_empty_password_and_locked(tmp_path: Path) -> None:
    # Empty password: decrypts cleanly
    empty_pdf = tmp_path / "empty_pw.pdf"
    empty_pdf.write_bytes(_make_encrypted_pdf(""))
    doc_empty = normalize_pdf(
        empty_pdf, "1" * 64, source_name="empty_pw.pdf", media_type="application/pdf"
    )
    assert doc_empty.metadata.get("pdf_unlocked_with_empty_password") is True
    assert doc_empty.metadata.get("pdf_locked") is not True

    # Real password: recognized as locked without crashing
    locked_pdf = tmp_path / "locked.pdf"
    locked_pdf.write_bytes(_make_encrypted_pdf("secret_password_123"))
    doc_locked = normalize_pdf(
        locked_pdf, "2" * 64, source_name="locked.pdf", media_type="application/pdf"
    )
    assert doc_locked.metadata.get("pdf_locked") is True
    assert doc_locked.metadata.get("archive_locked") is True
    assert len(doc_locked.segments) == 0


def test_pdf_fast_rendering_pypdfium2(tmp_path: Path) -> None:
    pdf_bytes = _make_two_column_pdf()
    pdf_path = tmp_path / "render_test.pdf"
    pdf_path.write_bytes(pdf_bytes)

    root = tmp_path / "derived_root"
    image_path, meta = _render_pdf_page(pdf_path, page=1, root=root, executable=None)
    assert image_path.is_file()
    assert meta["renderer"] == "pypdfium2"
    assert cast(int, meta["width"]) > 0
    assert cast(int, meta["height"]) > 0
