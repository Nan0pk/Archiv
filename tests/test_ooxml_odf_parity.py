"""OpenDocument twins of OOXML originals must recover the same content.

Each test builds an OOXML fixture and an OpenDocument twin carrying the same
content, normalizes both through the real ingestion path, and compares the
recoverable text and table rows. Locator key names are not compared across
families: the two vocabularies differ by design.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.text.paragraph import Paragraph
from openpyxl import Workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.shapes.graphfrm import GraphicFrame
from pptx.util import Inches
from typer.testing import CliRunner

import archiv.ingestion.normalize_odf as normalize_odf_module
from archiv.cli import app
from archiv.contracts import NormalizedDocument
from archiv.format_matrix import load_format_matrix
from archiv.hashing import sha256_file
from archiv.ingestion.normalizers import MalformedInputError, normalize

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
XLINK = "http://www.w3.org/1999/xlink"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
CHART = "urn:oasis:names:tc:opendocument:xmlns:chart:1.0"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _package(
    path: Path,
    mimetype: str,
    content: str,
    *,
    extra: dict[str, str] | None = None,
) -> None:
    manifest = (
        f'<manifest:manifest xmlns:manifest="{MANIFEST}" manifest:version="1.2">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mimetype, compress_type=ZIP_STORED)
        archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", manifest, compress_type=ZIP_DEFLATED)
        for name, data in (extra or {}).items():
            archive.writestr(name, data, compress_type=ZIP_DEFLATED)


def _content(
    body: str,
    *,
    presentation: bool = False,
    automatic_styles: str = "",
) -> str:
    extra = f' xmlns:draw="{DRAW}" xmlns:presentation="{PRESENTATION}"' if presentation else ""
    return (
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}" xmlns:xlink="{XLINK}" xmlns:style="{STYLE}"{extra} '
        f'office:version="1.2">{automatic_styles}'
        f"<office:body>{body}</office:body></office:document-content>"
    )


def _rows(*values: tuple[str, ...]) -> str:
    rows: list[str] = []
    for row in values:
        rows.append("<table:table-row>")
        for value in row:
            if value:
                rows.append(f"<table:table-cell><text:p>{value}</text:p></table:table-cell>")
            else:
                rows.append("<table:table-cell/>")
        rows.append("</table:table-row>")
    return "".join(rows)


def _texts(document: NormalizedDocument) -> list[str]:
    return [segment.text for segment in document.segments]


def _trimmed(row: list[object | None]) -> list[object | None]:
    end = len(row)
    while end and row[end - 1] is None:
        end -= 1
    return row[:end]


def _table_rows(document: NormalizedDocument) -> list[list[list[object | None]]]:
    # The two families store cell values with different native types (a number in
    # OOXML, a string attribute in OpenDocument); the content is what must match.
    return [
        [[None if value is None else str(value) for value in _trimmed(row)] for row in table.rows]
        for table in document.tables
    ]


def _add_hyperlink(
    paragraph: Paragraph,
    url: str | None,
    text: str,
    *,
    anchor: str | None = None,
) -> None:
    if anchor is not None:
        link = parse_xml(
            f'<w:hyperlink xmlns:w="{W}" w:anchor="{anchor}">'
            f"<w:r><w:t>{text}</w:t></w:r></w:hyperlink>"
        )
    else:
        assert url is not None
        relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = parse_xml(
            f'<w:hyperlink xmlns:w="{W}" xmlns:r="{R}" r:id="{relationship_id}">'
            f"<w:r><w:t>{text}</w:t></w:r></w:hyperlink>"
        )
    paragraph._p.append(link)  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]


def _add_note_reference(paragraph: Paragraph, tag: str, note_id: int) -> None:
    reference = parse_xml(f'<w:r xmlns:w="{W}"><w:{tag}Reference w:id="{note_id}"/></w:r>')
    paragraph._p.append(reference)  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]


def _note_part(tag: str, notes: dict[int, list[str]]) -> str:
    entries = [
        f'<w:{tag} w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:{tag}>',
        f'<w:{tag} w:type="continuationSeparator" w:id="0"><w:p><w:r>'
        f"<w:continuationSeparator/></w:r></w:p></w:{tag}>",
    ]
    for note_id, paragraphs in notes.items():
        body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
        entries.append(f'<w:{tag} w:id="{note_id}">{body}</w:{tag}>')
    return f'<w:{tag}s xmlns:w="{W}" xmlns:r="{R}">{"".join(entries)}</w:{tag}s>'


def _add_notes(
    path: Path,
    *,
    footnotes: dict[int, list[str]],
    endnotes: dict[int, list[str]],
) -> None:
    with ZipFile(path) as archive:
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}

    relationships = members["word/_rels/document.xml.rels"].decode("utf-8")
    extra = (
        f'<Relationship Id="rIdFoot" Type="{RT.FOOTNOTES}" Target="footnotes.xml"/>'
        f'<Relationship Id="rIdEnd" Type="{RT.ENDNOTES}" Target="endnotes.xml"/>'
    )
    members["word/_rels/document.xml.rels"] = relationships.replace(
        "</Relationships>", extra + "</Relationships>"
    ).encode("utf-8")

    content_types = members["[Content_Types].xml"].decode("utf-8")
    base = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    extra_types = (
        '<Override PartName="/word/footnotes.xml" '
        f'ContentType="{base}.footnotes+xml"/>'
        '<Override PartName="/word/endnotes.xml" '
        f'ContentType="{base}.endnotes+xml"/>'
    )
    members["[Content_Types].xml"] = content_types.replace(
        "</Types>", extra_types + "</Types>"
    ).encode("utf-8")

    members["word/footnotes.xml"] = _note_part("footnote", footnotes).encode("utf-8")
    members["word/endnotes.xml"] = _note_part("endnote", endnotes).encode("utf-8")
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _chart_content(
    *,
    title: str = "Sales by region",
    axis_title: str | None = "Units",
    label_address: str = "$Data.$B$1",
) -> str:
    axis = "" if axis_title is None else f"<chart:title><text:p>{axis_title}</text:p></chart:title>"
    return (
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}" xmlns:chart="{CHART}" office:version="1.2">'
        "<office:body><office:chart>"
        '<chart:chart chart:class="chart:bar">'
        f"<chart:title><text:p>{title}</text:p></chart:title>"
        "<chart:plot-area>"
        '<chart:axis chart:dimension="x"/>'
        f'<chart:axis chart:dimension="y">{axis}</chart:axis>'
        f'<chart:series chart:values-cell-range-address="$Data.$A$2:$A$3" '
        f'chart:label-cell-address="{label_address}"/>'
        "</chart:plot-area>"
        '<table:table table:name="Data">'
        "<table:table-row><table:table-cell/><table:table-cell>"
        "<text:p>Units sold</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:table-cell><text:p>North</text:p></table:table-cell>"
        '<table:table-cell office:value-type="float" office:value="10">'
        "<text:p>10</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:table-cell><text:p>South</text:p></table:table-cell>"
        '<table:table-cell office:value-type="float" office:value="20">'
        "<text:p>20</text:p></table:table-cell></table:table-row>"
        "</table:table>"
        "</chart:chart></office:chart></office:body></office:document-content>"
    )


def test_equivalent_documents_yield_equivalent_hyperlinks_and_notes(tmp_path: Path) -> None:
    original = tmp_path / "original.docx"
    document = Document()
    lead = document.add_paragraph("Lead paragraph")
    _add_hyperlink(lead, "https://example.com/parity", "External link")
    _add_hyperlink(lead, None, "Jump link", anchor="target")
    notes = document.add_paragraph("Note paragraph")
    _add_note_reference(notes, "footnote", 1)
    _add_note_reference(notes, "endnote", 1)
    document.save(str(original))
    _add_notes(
        original,
        footnotes={1: ["Footnote first", "Footnote second"]},
        endnotes={1: ["Endnote text"]},
    )

    twin = tmp_path / "twin.odt"
    _package(
        twin,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            "<text:p>Lead paragraph"
            '<text:a xlink:href="https://example.com/parity">External link</text:a>'
            '<text:a xlink:href="#target">Jump link</text:a>'
            "</text:p>"
            "<text:p>Note paragraph"
            '<text:note text:note-class="footnote" text:id="1">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body><text:p>Footnote first</text:p>"
            "<text:p>Footnote second</text:p></text:note-body>"
            "</text:note>"
            '<text:note text:note-class="endnote" text:id="1">'
            "<text:note-citation>2</text:note-citation>"
            "<text:note-body><text:p>Endnote text</text:p></text:note-body>"
            "</text:note>"
            "</text:p>"
            "</office:text>"
        ),
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert [segment.text for segment in right.segments if "hyperlink" in segment.locator] == [
        "https://example.com/parity",
        "#target",
    ]
    assert [
        (segment.locator, segment.text)
        for segment in right.segments
        if "footnote" in segment.locator or "endnote" in segment.locator
    ] == [
        ({"footnote": "1", "paragraph": 1}, "Footnote first"),
        ({"footnote": "1", "paragraph": 2}, "Footnote second"),
        ({"endnote": "1", "paragraph": 1}, "Endnote text"),
    ]
    assert right.metadata["external_links_ignored"] == 1


def test_equivalent_workbooks_yield_equivalent_merged_and_hidden_state(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.xlsx"
    workbook = Workbook()
    main = workbook.active
    assert main is not None
    main.title = "Main"
    main.merge_cells("A1:B1")
    main["A1"] = "Merged title"
    main["A2"] = "Visible"
    main["B2"] = "Hidden column"
    main["C2"] = "Link cell"
    main["C2"].hyperlink = "https://example.com/parity"
    main["A3"] = "Hidden row"
    main["B3"] = "Both"
    main["D4"] = "Extent"
    main.row_dimensions[3].hidden = True
    main.column_dimensions["B"].hidden = True

    hidden = workbook.create_sheet("HiddenSheet")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "Hidden sheet A1"
    hidden["B1"] = "Hidden link"
    hidden["B1"].hyperlink = "https://example.com/hidden"
    workbook.save(str(original))

    twin = tmp_path / "twin.ods"
    _package(
        twin,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-column/>"
            '<table:table-column table:visibility="collapse"/>'
            "<table:table-column/><table:table-column/>"
            "<table:table-row>"
            '<table:table-cell table:number-columns-spanned="2">'
            "<text:p>Merged title</text:p></table:table-cell>"
            "<table:covered-table-cell/>"
            "</table:table-row>"
            "<table:table-row>"
            "<table:table-cell><text:p>Visible</text:p></table:table-cell>"
            "<table:table-cell><text:p>Hidden column</text:p></table:table-cell>"
            "<table:table-cell><text:p>"
            '<text:a xlink:href="https://example.com/parity">Link cell</text:a>'
            "</text:p></table:table-cell>"
            "<table:table-cell/>"
            "</table:table-row>"
            '<table:table-row table:visibility="collapse">'
            "<table:table-cell><text:p>Hidden row</text:p></table:table-cell>"
            "<table:table-cell><text:p>Both</text:p></table:table-cell>"
            "<table:table-cell/><table:table-cell/>"
            "</table:table-row>"
            "<table:table-row>"
            '<table:table-cell table:number-columns-repeated="3"/>'
            "<table:table-cell><text:p>Extent</text:p></table:table-cell>"
            "</table:table-row>"
            "</table:table>"
            '<table:table table:name="HiddenSheet" table:style-name="ta1">'
            "<table:table-row>"
            "<table:table-cell><text:p>Hidden sheet A1</text:p></table:table-cell>"
            "<table:table-cell><text:p>"
            '<text:a xlink:href="https://example.com/hidden">Hidden link</text:a>'
            "</text:p></table:table-cell>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>",
            automatic_styles=(
                "<office:automatic-styles>"
                '<style:style style:name="ta1" style:family="table">'
                '<style:table-properties table:display="false"/>'
                "</style:style>"
                "</office:automatic-styles>"
            ),
        ),
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert _table_rows(left) == _table_rows(right)
    assert right.metadata["hidden_sheets"] == ["HiddenSheet"]
    assert [
        (segment.text, segment.locator)
        for segment in right.segments
        if ("merged" in segment.locator or "hidden" in segment.locator)
        and "hyperlink" not in segment.locator
    ] == [
        ("Merged title", {"sheet": "Main", "row": 1, "column": 1, "merged": "A1:B1"}),
        ("Hidden column", {"sheet": "Main", "row": 2, "column": 2, "hidden": "column"}),
        ("Hidden row", {"sheet": "Main", "row": 3, "column": 1, "hidden": "row"}),
        (
            "Both",
            {
                "sheet": "Main",
                "row": 3,
                "column": 2,
                "hidden": "row and column",
            },
        ),
        ("Hidden sheet A1", {"sheet": "HiddenSheet", "row": 1, "column": 1, "hidden": "sheet"}),
        ("Hidden link", {"sheet": "HiddenSheet", "row": 1, "column": 2, "hidden": "sheet"}),
    ]
    assert [
        (segment.text, segment.locator)
        for segment in right.segments
        if "hyperlink" in segment.locator
    ] == [
        (
            "https://example.com/parity",
            {"sheet": "Main", "row": 2, "column": 3, "hyperlink": 1},
        ),
        (
            "https://example.com/hidden",
            {"sheet": "HiddenSheet", "row": 1, "column": 2, "hyperlink": 1, "hidden": "sheet"},
        ),
    ]


def test_equivalent_presentations_yield_equivalent_charts_groups_and_hidden_marks(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    title = slide.shapes.title
    assert title is not None
    title.text = "Parity title"

    group = slide.shapes.add_group_shape()
    grouped = group.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    assert grouped.text_frame is not None
    grouped.text_frame.text = "Grouped text"

    data = CategoryChartData()
    data.categories = ["North", "South"]
    data.add_series("Units sold", (10, 20))  # pyright: ignore[reportUnknownMemberType]
    # python-pptx annotates add_chart as returning a chart, but returns its frame.
    frame = cast(
        GraphicFrame,
        slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(1),
            Inches(3),
            Inches(4),
            Inches(3),
            data,  # pyright: ignore[reportArgumentType]
        ),
    )
    chart = frame.chart
    chart.has_title = True
    assert chart.chart_title is not None
    chart.chart_title.text_frame.text = "Sales by region"
    chart.value_axis.has_title = True
    assert chart.value_axis.axis_title is not None
    chart.value_axis.axis_title.text_frame.text = "Units"

    hidden_shape = slide.shapes.add_textbox(Inches(5), Inches(1), Inches(3), Inches(1))
    assert hidden_shape.text_frame is not None
    hidden_shape.text_frame.text = "Hidden shape text"
    properties = hidden_shape.element.xpath("./*[1]/p:cNvPr")  # pyright: ignore[reportUnknownMemberType]
    properties[0].set("hidden", "1")  # pyright: ignore[reportUnknownMemberType]

    hidden_slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    hidden_text = hidden_slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    assert hidden_text.text_frame is not None
    hidden_text.text_frame.text = "Hidden slide text"
    hidden_slide.element.set("show", "0")  # pyright: ignore[reportUnknownMemberType]
    presentation.save(str(original))

    twin = tmp_path / "twin.odp"
    _package(
        twin,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:layer-set><draw:layer draw:name="hidden-layer" '
            'draw:display="none"/></draw:layer-set>'
            '<draw:page draw:name="Slide 1">'
            '<draw:frame presentation:class="title"><draw:text-box>'
            "<text:p>Parity title</text:p>"
            "</draw:text-box></draw:frame>"
            "<draw:g><draw:frame><draw:text-box>"
            "<text:p>Grouped text</text:p>"
            "</draw:text-box></draw:frame></draw:g>"
            '<draw:frame><draw:object xlink:href="./Object 1"/></draw:frame>'
            '<draw:frame draw:layer="hidden-layer"><draw:text-box>'
            "<text:p>Hidden shape text</text:p>"
            "</draw:text-box></draw:frame>"
            "</draw:page>"
            '<draw:page draw:name="Slide 2" draw:style-name="dp1">'
            "<draw:frame><draw:text-box>"
            "<text:p>Hidden slide text</text:p>"
            "</draw:text-box></draw:frame>"
            "</draw:page>"
            "</office:presentation>",
            presentation=True,
            automatic_styles=(
                "<office:automatic-styles>"
                '<style:style style:name="dp1" style:family="drawing-page">'
                '<style:drawing-page-properties presentation:visibility="hidden"/>'
                "</style:style>"
                "</office:automatic-styles>"
            ),
        ),
        extra={"Object 1/content.xml": _chart_content()},
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert [(segment.locator, segment.text) for segment in right.segments] == [
        ({"slide": 1, "object": 1, "placeholder": "title"}, "Parity title"),
        ({"slide": 1, "object": 2, "grouped_shape": "1"}, "Grouped text"),
        (
            {"slide": 1, "object": 3, "chart": 1},
            "Sales by region\nUnits\nUnits sold",
        ),
        ({"slide": 1, "object": 4, "hidden": "shape"}, "Hidden shape text"),
        ({"slide": 2, "object": 1, "hidden": "slide"}, "Hidden slide text"),
    ]
    assert right.metadata["hidden_slides"] == 1
    assert right.metadata["charts"] == 1


def test_note_body_tables_and_link_targets_are_read(tmp_path: Path) -> None:
    path = tmp_path / "note-table.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text><text:p>Body"
            '<text:note text:note-class="footnote" text:id="n1">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body>"
            "<text:p>Note intro</text:p>"
            '<table:table table:name="NoteTable">'
            "<table:table-row><table:table-cell><text:p>"
            '<text:a xlink:href="https://example.com/note">Note link</text:a>'
            "</text:p></table:table-cell></table:table-row>"
            "</table:table>"
            "</text:note-body>"
            "</text:note>"
            "</text:p></office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"paragraph": 1}, "Body"),
        ({"footnote": "n1", "paragraph": 1}, "Note intro"),
        ({"footnote": "n1", "table": 1, "row": 1, "column": 1}, "Note link"),
        (
            {"footnote": "n1", "table": 1, "row": 1, "column": 1, "hyperlink": 1},
            "https://example.com/note",
        ),
    ]
    assert document.tables[0].locator == {"footnote": "n1", "table": 1}
    assert document.tables[0].rows == [["Note link"]]


def test_text_table_links_follow_their_cells(tmp_path: Path) -> None:
    path = tmp_path / "table-links.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            '<office:text><table:table table:name="Links"><table:table-row>'
            "<table:table-cell><text:p>"
            '<text:a xlink:href="https://example.com/one">One</text:a>'
            "</text:p></table:table-cell>"
            "<table:table-cell><text:p>"
            '<text:a xlink:href="https://example.com/two">Two</text:a>'
            "</text:p></table:table-cell>"
            "</table:table-row></table:table></office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"table": 1, "row": 1, "column": 1}, "One"),
        (
            {"table": 1, "row": 1, "column": 1, "hyperlink": 1},
            "https://example.com/one",
        ),
        ({"table": 1, "row": 1, "column": 2}, "Two"),
        (
            {"table": 1, "row": 1, "column": 2, "hyperlink": 1},
            "https://example.com/two",
        ),
    ]


@pytest.mark.parametrize(
    ("row_attributes", "cell_attributes"),
    [
        (' table:number-rows-repeated="2"', ' table:number-rows-spanned="2"'),
        ("", ' table:number-columns-repeated="2" table:number-columns-spanned="2"'),
    ],
    ids=["repeated-row", "repeated-column"],
)
def test_repeated_merged_spreadsheet_cell_is_refused(
    tmp_path: Path, row_attributes: str, cell_attributes: str
) -> None:
    path = tmp_path / "repeated-merge.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            f'<table:table table:name="Main"><table:table-row{row_attributes}>'
            f"<table:table-cell{cell_attributes}><text:p>Anchor</text:p></table:table-cell>"
            "</table:table-row></table:table>"
            "</office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="repeats a merged cell"):
        normalize(path, sha256_file(path))


@pytest.mark.parametrize("href", ["./Object 9", "https://example.com/chart"])
def test_presentation_chart_with_a_missing_or_external_object_is_skipped(
    tmp_path: Path, href: str
) -> None:
    path = tmp_path / "missing-chart.odp"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation><draw:page>"
            "<draw:frame><draw:text-box><text:p>Title</text:p></draw:text-box></draw:frame>"
            f'<draw:frame><draw:object xlink:href="{href}"/></draw:frame>'
            "</draw:page></office:presentation>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Title"]
    assert document.metadata["charts"] == 0
    assert document.metadata["external_links_ignored"] == (1 if href.startswith("http") else 0)


def test_presentation_chart_with_an_unresolvable_series_label_is_skipped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unresolved-label.odp"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation><draw:page>"
            '<draw:frame><draw:object xlink:href="./Object 1"/></draw:frame>'
            "</draw:page></office:presentation>",
            presentation=True,
        ),
        extra={"Object 1/content.xml": _chart_content(label_address="$Data.$Z$99")},
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Sales by region\nUnits"]


def test_group_nesting_limit_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "deep-groups.odp"
    body = "<draw:frame><draw:text-box><text:p>Deep</text:p></draw:text-box></draw:frame>"
    for _ in range(33):
        body = f"<draw:g>{body}</draw:g>"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            f"<office:presentation><draw:page>{body}</draw:page></office:presentation>",
            presentation=True,
        ),
    )

    with pytest.raises(MalformedInputError, match="group nesting limit exceeded"):
        normalize(path, sha256_file(path))


def test_hidden_slide_notes_carry_the_slide_marker(tmp_path: Path) -> None:
    path = tmp_path / "hidden-notes.odp"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:page draw:style-name="dp1">'
            "<presentation:notes>"
            '<draw:frame presentation:class="notes"><draw:text-box>'
            "<text:p>Hidden note</text:p>"
            "</draw:text-box></draw:frame>"
            "</presentation:notes>"
            "</draw:page></office:presentation>",
            presentation=True,
            automatic_styles=(
                "<office:automatic-styles>"
                '<style:style style:name="dp1" style:family="drawing-page">'
                '<style:drawing-page-properties presentation:visibility="hidden"/>'
                "</style:style>"
                "</office:automatic-styles>"
            ),
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"slide": 1, "speaker_notes": 1, "hidden": "slide"}, "Hidden note")
    ]


def test_hidden_drawing_page_carries_the_page_marker(tmp_path: Path) -> None:
    path = tmp_path / "hidden-page.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:style-name="dp1"><draw:frame><draw:text-box>'
            "<text:p>Hidden page text</text:p>"
            "</draw:text-box></draw:frame></draw:page>"
            "</office:drawing>",
            presentation=True,
            automatic_styles=(
                "<office:automatic-styles>"
                '<style:style style:name="dp1" style:family="drawing-page">'
                '<style:drawing-page-properties presentation:visibility="hidden"/>'
                "</style:style>"
                "</office:automatic-styles>"
            ),
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"page": 1, "object": 1, "hidden": "page"}, "Hidden page text")
    ]
    assert document.metadata["hidden_pages"] == 1


@pytest.mark.parametrize("family", ["presentation", "drawing"])
def test_page_locator_combinations_match_the_matrix(tmp_path: Path, family: str) -> None:
    page_key = "slide" if family == "presentation" else "page"
    suffix = "odp" if family == "presentation" else "odg"
    media_family = "presentation" if family == "presentation" else "graphics"
    mimetype = f"application/vnd.oasis.opendocument.{media_family}"
    matrix = load_format_matrix(Path(__file__).parents[1] / "docs/format-compatibility.json")
    claimed = {frozenset(shape) for shape in matrix.family_for_suffix(f".{suffix}").locator_shapes}
    seen: set[frozenset[str]] = set()
    for number, (grouped, hidden, placeholder, kind) in enumerate(
        product([False, True], [False, True], [False, True], ["text", "table", "chart"])
    ):
        attributes = ' presentation:class="outline"' if placeholder else ""
        contents = {
            "text": "<draw:text-box><text:p>Object text</text:p></draw:text-box>",
            "table": f'<table:table table:name="T">{_rows(("Cell text",))}</table:table>',
            "chart": '<draw:object xlink:href="./Object 1"/>',
        }[kind]
        shape = f"<draw:frame{attributes}>{contents}</draw:frame>"
        if grouped:
            shape = f"<draw:g><draw:g>{shape}</draw:g></draw:g>"
        path = tmp_path / f"combination-{number}.{suffix}"
        _package(
            path,
            mimetype,
            _content(
                f'<office:{family}><draw:page draw:style-name="dp">'
                f"{shape}</draw:page></office:{family}>",
                presentation=True,
                automatic_styles=(
                    '<office:automatic-styles><style:style style:name="dp" '
                    'style:family="drawing-page"><style:drawing-page-properties '
                    f'presentation:visibility="{"hidden" if hidden else "visible"}"/>'
                    "</style:style></office:automatic-styles>"
                ),
            ),
            extra={"Object 1/content.xml": _chart_content()},
        )
        document = normalize(path, sha256_file(path))
        expected: dict[str, object] = {page_key: 1, "object": 1}
        if grouped:
            expected["grouped_shape"] = "1.1"
        if hidden:
            expected["hidden"] = page_key
        if placeholder:
            expected["placeholder"] = "body"
        if kind == "chart":
            expected["chart"] = 1
        elif kind == "table":
            expected.update(row=1, column=1)
        assert len(document.segments) == 1
        assert document.segments[0].locator == expected
        seen.add(frozenset(expected))
        if kind == "chart":
            assert document.segments[0].text == "Sales by region\nUnits\nUnits sold"
    assert seen <= claimed
    assert claimed - seen == (
        {frozenset({"slide", "speaker_notes"}), frozenset({"slide", "speaker_notes", "hidden"})}
        if family == "presentation"
        else set()
    )


def test_notes_without_ids_are_numbered_per_class(tmp_path: Path) -> None:
    path = tmp_path / "unnumbered-notes.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text><text:p>Body"
            '<text:note text:note-class="footnote">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body><text:p>First footnote</text:p></text:note-body>"
            "</text:note>"
            '<text:note text:note-class="endnote">'
            "<text:note-citation>2</text:note-citation>"
            "<text:note-body><text:p>First endnote</text:p></text:note-body>"
            "</text:note>"
            '<text:note text:note-class="footnote">'
            "<text:note-citation>3</text:note-citation>"
            "<text:note-body><text:p>Second footnote</text:p></text:note-body>"
            "</text:note>"
            "</text:p></office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"paragraph": 1}, "Body"),
        ({"footnote": "1", "paragraph": 1}, "First footnote"),
        ({"endnote": "1", "paragraph": 1}, "First endnote"),
        ({"footnote": "2", "paragraph": 1}, "Second footnote"),
    ]


def test_unknown_note_class_stays_inline(tmp_path: Path) -> None:
    path = tmp_path / "unknown-note.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text><text:p>Body"
            '<text:note text:note-class="comment" text:id="c1">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body><text:p>Comment "
            '<text:a xlink:href="https://example.com/comment">link</text:a>'
            "</text:p></text:note-body>"
            "</text:note>"
            "</text:p></office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert [(segment.locator, segment.text) for segment in document.segments] == [
        ({"paragraph": 1}, "Body1Comment link"),
        (
            {"paragraph": 1, "hyperlink": 1},
            "https://example.com/comment",
        ),
    ]


def test_equivalent_documents_yield_equivalent_recovery(tmp_path: Path) -> None:
    original = tmp_path / "original.docx"
    document = Document()
    document.add_heading("Parity heading", level=1)
    document.add_paragraph("Parity paragraph")
    table = document.add_table(rows=2, cols=2)
    for row in range(2):
        for column in range(2):
            table.cell(row, column).text = f"R{row + 1}C{column + 1}"
    document.save(str(original))

    twin = tmp_path / "twin.odt"
    _package(
        twin,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<text:h text:outline-level="1">Parity heading</text:h>'
            "<text:p>Parity paragraph</text:p>"
            '<table:table table:name="ParityTable">'
            f"{_rows(('R1C1', 'R1C2'), ('R2C1', 'R2C2'))}"
            "</table:table>"
            "</office:text>"
        ),
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert _table_rows(left) == _table_rows(right)
    assert right.tables[0].locator == {"table": 1}
    assert right.metadata["processor_version"] == "5"


def test_equivalent_workbooks_yield_equivalent_recovery(tmp_path: Path) -> None:
    original = tmp_path / "original.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Main"
    sheet["A1"] = "Marker"
    sheet["A2"] = 2
    sheet["B2"] = 3
    sheet["C2"] = "=A2+B2"
    workbook.save(str(original))

    # openpyxl writes the formula but not the result a spreadsheet application
    # stored beside it. Write that result into the package the way one exists
    # in a real workbook, so both readers index the number a person remembers.
    with ZipFile(original) as archive:
        items = [(info, archive.read(info.filename)) for info in archive.infolist()]
    with ZipFile(original, "w", ZIP_DEFLATED) as archive:
        for info, data in items:
            if info.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b"<f>A2+B2</f><v></v>", b"<f>A2+B2</f><v>5</v>")
            archive.writestr(info, data)

    twin = tmp_path / "twin.ods"
    _package(
        twin,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            '<table:table-column table:number-columns-repeated="3"/>'
            "<table:table-row>"
            '<table:table-cell office:value-type="string">'
            "<text:p>Marker</text:p></table:table-cell>"
            "<table:table-cell/><table:table-cell/>"
            "</table:table-row>"
            "<table:table-row>"
            '<table:table-cell office:value-type="float" office:value="2">'
            "<text:p>2</text:p></table:table-cell>"
            '<table:table-cell office:value-type="float" office:value="3">'
            "<text:p>3</text:p></table:table-cell>"
            '<table:table-cell table:formula="of:=[.A2]+[.B2]" office:value-type="float" '
            'office:value="5"><text:p>5</text:p></table:table-cell>'
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert _table_rows(left) == _table_rows(right)


def test_equivalent_presentations_yield_equivalent_recovery(tmp_path: Path) -> None:
    original = tmp_path / "original.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    title = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    assert title.text_frame is not None
    title.text_frame.text = "Slide title"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    assert table is not None
    for row in range(2):
        for column in range(2):
            table.cell(row, column).text = f"T{row + 1}C{column + 1}"
    notes = slide.notes_slide
    assert notes is not None
    assert notes.notes_text_frame is not None
    notes.notes_text_frame.text = "Speaker note marker"
    presentation.save(str(original))

    twin = tmp_path / "twin.odp"
    _package(
        twin,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:page draw:name="Slide 1">'
            '<draw:frame draw:name="Title"><draw:text-box>'
            "<text:p>Slide title</text:p>"
            "</draw:text-box></draw:frame>"
            '<draw:frame draw:name="Table"><table:table table:name="ParityTable">'
            f"{_rows(('T1C1', 'T1C2'), ('T2C1', 'T2C2'))}"
            "</table:table></draw:frame>"
            "<presentation:notes>"
            '<draw:frame presentation:class="page"><draw:image '
            'xlink:href="Pictures/slide.png"/></draw:frame>'
            '<draw:frame presentation:class="notes"><draw:text-box>'
            "<text:p>Speaker note marker</text:p>"
            "</draw:text-box></draw:frame>"
            '<draw:frame presentation:class="page-number"><draw:text-box>'
            "<text:p>1</text:p>"
            "</draw:text-box></draw:frame>"
            "</presentation:notes>"
            "</draw:page>"
            "</office:presentation>",
            presentation=True,
        ),
    )

    left = normalize(original, sha256_file(original))
    right = normalize(twin, sha256_file(twin))

    assert _texts(left) == _texts(right)
    assert _table_rows(left) == _table_rows(right)
    assert right.tables[0].locator == {"slide": 1, "object": 2}
    assert right.metadata["presentation_notes_extracted"] is True


def test_nested_text_table_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "nested.odt"
    nested = (
        '<table:table table:name="Inner"><table:table-row><table:table-cell>'
        "<text:p>Inner</text:p></table:table-cell></table:table-row></table:table>"
    )
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Outer"><table:table-row><table:table-cell>'
            f"<text:p>Outer</text:p>{nested}"
            "</table:table-cell></table:table-row></table:table>"
            "</office:text>"
        ),
    )

    with pytest.raises(MalformedInputError, match="nests a table"):
        normalize(path, sha256_file(path))


def test_text_table_row_budget_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(normalize_odf_module, "MAX_EXPANDED_ROWS", 1)
    path = tmp_path / "too-many-rows.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="TooManyRows">'
            f"{_rows(('First',), ('Second',))}"
            "</table:table>"
            "</office:text>"
        ),
    )

    with pytest.raises(MalformedInputError, match="ODF table expanded row limit exceeded"):
        normalize(path, sha256_file(path))


def test_text_table_cell_does_not_repeat_footnote_text(tmp_path: Path) -> None:
    path = tmp_path / "cell-footnote.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="CellNote">'
            "<table:table-row><table:table-cell>"
            "<text:p>Cell text"
            '<text:note text:note-class="footnote">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body><text:p>FOOTNOTE TEXT</text:p></text:note-body>"
            "</text:note>"
            "</text:p>"
            "</table:table-cell></table:table-row>"
            "</table:table>"
            "</office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Cell text", "FOOTNOTE TEXT"]
    assert document.tables[0].rows == [["Cell text"]]
    assert document.segments[1].locator == {"footnote": "1", "paragraph": 1}


def test_table_cell_joined_character_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalize_odf_module, "MAX_SEGMENT_CHARACTERS", 5)
    path = tmp_path / "long-cell.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="LongCell">'
            "<table:table-row><table:table-cell>"
            "<text:p>abcd</text:p><text:p>efgh</text:p>"
            "</table:table-cell></table:table-row>"
            "</table:table>"
            "</office:text>"
        ),
    )

    with pytest.raises(MalformedInputError, match="character limit exceeded"):
        normalize(path, sha256_file(path))


def test_table_cell_attribute_character_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalize_odf_module, "MAX_SEGMENT_CHARACTERS", 5)
    path = tmp_path / "long-attribute.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="LongAttribute">'
            "<table:table-row>"
            '<table:table-cell office:value-type="string" office:string-value="abcdef"/>'
            "</table:table-row>"
            "</table:table>"
            "</office:text>"
        ),
    )

    with pytest.raises(MalformedInputError, match="character limit exceeded"):
        normalize(path, sha256_file(path))


def test_spreadsheet_cell_attribute_character_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalize_odf_module, "MAX_SEGMENT_CHARACTERS", 5)
    path = tmp_path / "long-attribute.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            '<table:table-cell office:value-type="string" office:string-value="abcdef"/>'
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="character limit exceeded"):
        normalize(path, sha256_file(path))


def test_drawing_page_table_is_read(tmp_path: Path) -> None:
    path = tmp_path / "drawing.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="Table"><table:table table:name="DrawingTable">'
            f"{_rows(('D1', 'D2'))}"
            "</table:table></draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert document.tables[0].locator == {"page": 1, "object": 1}
    assert document.tables[0].rows == [["D1", "D2"]]
    assert [(segment.text, segment.locator) for segment in document.segments] == [
        ("D1", {"page": 1, "object": 1, "row": 1, "column": 1}),
        ("D2", {"page": 1, "object": 1, "row": 1, "column": 2}),
    ]


def test_drawing_frame_keeps_caption_text_outside_its_table(tmp_path: Path) -> None:
    path = tmp_path / "caption.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="TableFrame">'
            '<table:table table:name="CaptionTable">'
            f"{_rows(('Cell',))}"
            "</table:table>"
            "<draw:caption><draw:text-box>"
            "<text:p>CAPTION TEXT</text:p>"
            "</draw:text-box></draw:caption>"
            "</draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Cell", "CAPTION TEXT"]
    assert document.tables[0].rows == [["Cell"]]


def test_text_table_header_rows_and_row_groups_are_read(tmp_path: Path) -> None:
    path = tmp_path / "grouped.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Grouped">'
            "<table:table-header-rows><table:table-row>"
            "<table:table-cell><text:p>Header</text:p></table:table-cell>"
            "</table:table-row></table:table-header-rows>"
            "<table:table-row-group><table:table-row>"
            "<table:table-cell><text:p>Body</text:p></table:table-cell>"
            "</table:table-row></table:table-row-group>"
            "</table:table>"
            "</office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert document.tables[0].rows == [["Header"], ["Body"]]
    assert [(segment.text, segment.locator) for segment in document.segments] == [
        ("Header", {"table": 1, "row": 1, "column": 1}),
        ("Body", {"table": 1, "row": 2, "column": 1}),
    ]


def test_table_cell_is_found_by_search_in_both_families(tmp_path: Path) -> None:
    text_path = tmp_path / "text-twin.odt"
    _package(
        text_path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="TextTable">'
            f"{_rows(('A', 'B'), ('C', 'ARCHIV-ODF-TEXT-MARKER-2026'))}"
            "</table:table>"
            "</office:text>"
        ),
    )
    presentation_path = tmp_path / "slide-twin.odp"
    _package(
        presentation_path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:page draw:name="Slide 1">'
            '<draw:frame draw:name="Title"><draw:text-box>'
            "<text:p>Slide title</text:p>"
            "</draw:text-box></draw:frame>"
            '<draw:frame draw:name="Table"><table:table table:name="SlideTable">'
            f"{_rows(('A', 'ARCHIV-ODF-SLIDE-MARKER-2026'))}"
            "</table:table></draw:frame>"
            "</draw:page>"
            "</office:presentation>",
            presentation=True,
        ),
    )

    home = tmp_path / "home"
    runner = CliRunner()
    for path in (text_path, presentation_path):
        ingested = runner.invoke(app, ["ingest", str(path), "--home", str(home)])
        assert ingested.exit_code == 0, ingested.output
    rebuilt = runner.invoke(app, ["rebuild-search-index", "--home", str(home)])
    assert rebuilt.exit_code == 0, rebuilt.output

    for query, locator in (
        ("ARCHIV-ODF-TEXT-MARKER-2026", {"table": 1, "row": 2, "column": 2}),
        ("ARCHIV-ODF-SLIDE-MARKER-2026", {"slide": 1, "object": 2, "row": 1, "column": 2}),
    ):
        found = runner.invoke(app, ["search", query, "--home", str(home)])
        assert found.exit_code == 0, found.output
        payload = json.loads(found.output)
        assert [hit["citation"]["locator"] for hit in payload] == [locator]


def test_presentation_table_rows_are_padded_to_the_widest_row(tmp_path: Path) -> None:
    path = tmp_path / "ragged.odp"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:page draw:name="Slide 1">'
            '<draw:frame draw:name="Table"><table:table table:name="Ragged">'
            "<table:table-row><table:table-cell><text:p>A</text:p></table:table-cell>"
            "<table:table-cell><text:p>B</text:p></table:table-cell></table:table-row>"
            "<table:table-row><table:table-cell><text:p>C</text:p></table:table-cell>"
            "</table:table-row>"
            "</table:table></draw:frame>"
            "</draw:page>"
            "</office:presentation>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert document.tables[0].rows == [["A", "B"], ["C", None]]


def test_notes_page_with_only_furniture_yields_no_notes_segments(tmp_path: Path) -> None:
    path = tmp_path / "furniture.odp"
    _package(
        path,
        "application/vnd.oasis.opendocument.presentation",
        _content(
            "<office:presentation>"
            '<draw:page draw:name="Slide 1">'
            '<draw:frame draw:name="Title"><draw:text-box>'
            "<text:p>Slide title</text:p>"
            "</draw:text-box></draw:frame>"
            "<presentation:notes>"
            '<draw:frame presentation:class="page"><draw:image '
            'xlink:href="Pictures/slide.png"/></draw:frame>'
            '<draw:frame presentation:class="page-number"><draw:text-box>'
            "<text:p>1</text:p>"
            "</draw:text-box></draw:frame>"
            "</presentation:notes>"
            "</draw:page>"
            "</office:presentation>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Slide title"]
    assert all("speaker_notes" not in segment.locator for segment in document.segments)
    assert document.metadata["presentation_notes_extracted"] is False


def test_spreadsheet_formula_only_cell_character_limit_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalize_odf_module, "MAX_SEGMENT_CHARACTERS", 5)
    path = tmp_path / "long-formula.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            '<table:table-cell table:formula="of:=SUM([.A1:.A99])"/>'
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="character limit exceeded"):
        normalize(path, sha256_file(path))


def test_link_with_no_visible_text_is_recovered_from_paragraph_and_cell(
    tmp_path: Path,
) -> None:
    paragraph_path = tmp_path / "link-paragraph.odt"
    _package(
        paragraph_path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<text:p><text:a xlink:href="https://example.com/empty"/></text:p>'
            "</office:text>"
        ),
    )
    cell_path = tmp_path / "link-cell.odt"
    _package(
        cell_path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Links">'
            "<table:table-row><table:table-cell>"
            '<text:p><text:a xlink:href="https://example.com/cell"/></text:p>'
            "</table:table-cell></table:table-row>"
            "</table:table>"
            "</office:text>"
        ),
    )

    assert _texts(normalize(paragraph_path, sha256_file(paragraph_path))) == [
        "https://example.com/empty"
    ]
    assert _texts(normalize(cell_path, sha256_file(cell_path))) == ["https://example.com/cell"]


def test_note_table_inside_a_table_cell_is_read_without_allowing_nested_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "footnote-table.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Outer"><table:table-row><table:table-cell>'
            "<text:p>Outer"
            '<text:note text:note-class="footnote">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body>"
            '<table:table table:name="NoteTable"><table:table-row>'
            "<table:table-cell><text:p>NOTE CELL</text:p></table:table-cell>"
            "</table:table-row></table:table>"
            "</text:note-body>"
            "</text:note>"
            "</text:p>"
            "</table:table-cell></table:table-row></table:table>"
            "</office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Outer", "NOTE CELL"]

    nested_path = tmp_path / "nested.odt"
    _package(
        nested_path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Outer"><table:table-row><table:table-cell>'
            '<table:table table:name="Inner"><table:table-row>'
            "<table:table-cell><text:p>Inner</text:p></table:table-cell>"
            "</table:table-row></table:table>"
            "</table:table-cell></table:table-row></table:table>"
            "</office:text>"
        ),
    )

    with pytest.raises(MalformedInputError, match="nests a table"):
        normalize(nested_path, sha256_file(nested_path))


def test_caption_before_a_table_keeps_document_order(tmp_path: Path) -> None:
    path = tmp_path / "caption-before.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="TableFrame">'
            "<draw:caption><draw:text-box>"
            "<text:p>CAPTION FIRST</text:p>"
            "</draw:text-box></draw:caption>"
            '<table:table table:name="CaptionTable">'
            f"{_rows(('Cell',))}"
            "</table:table>"
            "</draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["CAPTION FIRST", "Cell"]


def test_caption_after_a_table_keeps_document_order(tmp_path: Path) -> None:
    path = tmp_path / "caption-after.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="TableFrame">'
            '<table:table table:name="CaptionTable">'
            f"{_rows(('Cell',))}"
            "</table:table>"
            "<draw:caption><draw:text-box>"
            "<text:p>CAPTION AFTER</text:p>"
            "</draw:text-box></draw:caption>"
            "</draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Cell", "CAPTION AFTER"]


def test_two_tables_in_one_frame_are_both_read_in_order(tmp_path: Path) -> None:
    path = tmp_path / "two-tables.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="TableFrame">'
            '<table:table table:name="First">'
            f"{_rows(('First',))}"
            "</table:table>"
            "<text:p>Between</text:p>"
            '<table:table table:name="Second">'
            f"{_rows(('Second',))}"
            "</table:table>"
            "</draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["First", "Between", "Second"]


def test_frame_text_keeps_footnote_text_as_before(tmp_path: Path) -> None:
    path = tmp_path / "frame-note.odg"
    _package(
        path,
        "application/vnd.oasis.opendocument.graphics",
        _content(
            "<office:drawing>"
            '<draw:page draw:name="Page 1">'
            '<draw:frame draw:name="TableFrame">'
            '<table:table table:name="FrameTable">'
            f"{_rows(('Cell',))}"
            "</table:table>"
            "<text:p>Body"
            '<text:note text:note-class="footnote">'
            "<text:note-citation>1</text:note-citation>"
            "<text:note-body><text:p>NOTE TEXT</text:p></text:note-body>"
            "</text:note>"
            "</text:p>"
            "</draw:frame>"
            "</draw:page>"
            "</office:drawing>",
            presentation=True,
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document)[0] == "Cell"
    assert "NOTE TEXT" in _texts(document)[1]


def test_link_on_a_merged_anchor_cell_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "anchor-link.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _content(
            "<office:text>"
            '<table:table table:name="Merged">'
            "<table:table-row>"
            '<table:table-cell table:number-columns-spanned="2">'
            '<text:p><text:a xlink:href="https://example.com/anchor"/></text:p>'
            "</table:table-cell>"
            "<table:covered-table-cell/>"
            "</table:table-row>"
            "</table:table>"
            "</office:text>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["https://example.com/anchor"]


def test_padded_spreadsheet_rows_with_only_empty_trailing_repeats_are_read(
    tmp_path: Path,
) -> None:
    path = tmp_path / "padded.ods"
    rows = "".join(
        "<table:table-row>"
        "<table:table-cell><text:p>Content</text:p></table:table-cell>"
        "<table:table-cell table:number-columns-repeated='16383'/>"
        "</table:table-row>"
        for _ in range(4)
    )
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            f'<table:table table:name="Main">{rows}</table:table>'
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Content"] * 4


def test_middle_empty_cells_still_count_in_a_spreadsheet_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "middle-empty.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            "<table:table-cell><text:p>First</text:p></table:table-cell>"
            "<table:table-cell table:number-columns-repeated='3'/>"
            "<table:table-cell><text:p>Last</text:p></table:table-cell>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["First", "Last"]
    assert [segment.locator["column"] for segment in document.segments] == [1, 5]
    assert _table_rows(document) == [[["First", None, None, None, "Last"]]]


def test_large_row_repeat_with_trailing_padding_stays_bounded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large-repeat.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row table:number-rows-repeated='10000'>"
            "<table:table-cell><text:p>Content</text:p></table:table-cell>"
            "<table:table-cell table:number-columns-repeated='16383'/>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Content"] * 10000


def test_large_row_repeat_with_only_empty_padding_reads_quickly(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty-repeat.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row table:number-rows-repeated='10000'>"
            "<table:table-cell table:number-columns-repeated='16384'/>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == []


def test_far_right_content_after_padding_still_hits_the_cell_budget(
    tmp_path: Path,
) -> None:
    path = tmp_path / "far-right.ods"
    rows = "".join(
        "<table:table-row>"
        "<table:table-cell table:number-columns-repeated='16383'/>"
        "<table:table-cell><text:p>Far</text:p></table:table-cell>"
        "</table:table-row>"
        for _ in range(4)
    )
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            f'<table:table table:name="Main">{rows}</table:table>'
            "</office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="expanded cell limit exceeded"):
        normalize(path, sha256_file(path))


def test_formula_only_cell_after_padding_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "formula-padded.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            "<table:table-cell table:number-columns-repeated='3'/>"
            '<table:table-cell table:formula="of:=SUM([.A1:.A99])"/>'
            "<table:table-cell table:number-columns-repeated='16380'/>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["of:=SUM([.A1:.A99])"]
    assert [segment.locator["column"] for segment in document.segments] == [4]


def test_link_only_cell_after_padding_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "link-padded.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            "<table:table-cell table:number-columns-repeated='3'/>"
            '<table:table-cell><text:p><text:a xlink:href="https://example.com/padded-link"/></text:p></table:table-cell>'
            "<table:table-cell table:number-columns-repeated='16380'/>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["https://example.com/padded-link"]
    assert [segment.locator["column"] for segment in document.segments] == [4]


def test_merged_anchor_after_padding_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "merged-padded.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _content(
            "<office:spreadsheet>"
            '<table:table table:name="Main">'
            "<table:table-row>"
            "<table:table-cell table:number-columns-repeated='3'/>"
            '<table:table-cell table:number-columns-spanned="2">'
            "<text:p>Merged</text:p></table:table-cell>"
            "<table:covered-table-cell/>"
            "<table:table-cell table:number-columns-repeated='16379'/>"
            "</table:table-row>"
            "</table:table>"
            "</office:spreadsheet>"
        ),
    )

    document = normalize(path, sha256_file(path))

    assert _texts(document) == ["Merged"]
    assert [segment.locator["column"] for segment in document.segments] == [4]
