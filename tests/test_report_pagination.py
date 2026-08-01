from __future__ import annotations

from typing import Any, cast

from docx.oxml.ns import qn

from archiv.reports.generator import _build_document  # pyright: ignore[reportPrivateUsage]


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
    paragraph_format = cast(Any, appendix.paragraph_format)
    document_element = cast(Any, document.element)
    page_breaks = [
        element
        for element in document_element.iter(qn("w:br"))
        if element.get(qn("w:type")) == "page"
    ]

    assert paragraph_format.page_break_before is True
    assert page_breaks == []
