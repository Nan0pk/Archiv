from __future__ import annotations

from docx.oxml.ns import qn

from archiv.reports.generator import _build_document


def test_source_appendix_uses_page_break_before_without_blank_break_paragraph() -> None:
    document = _build_document(
        title="Public field-trial report",
        query="status evidence",
        report_id="report-id",
        sources=[],
    )

    appendix = next(
        paragraph for paragraph in document.paragraphs if paragraph.text == "Source Appendix"
    )

    assert appendix.paragraph_format.page_break_before is True
    assert list(document.element.iter(qn("w:br"))) == []
