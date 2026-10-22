"""OpenDocument twins of OOXML originals must recover the same content.

Each test builds an OOXML fixture and an OpenDocument twin carrying the same
content, normalizes both through the real ingestion path, and compares the
recoverable text and table rows. Locator key names are not compared across
families: the two vocabularies differ by design.
"""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
from typer.testing import CliRunner

import archiv.ingestion.normalize_odf as normalize_odf_module
from archiv.cli import app
from archiv.contracts import NormalizedDocument
from archiv.hashing import sha256_file
from archiv.ingestion.normalizers import MalformedInputError, normalize

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
XLINK = "http://www.w3.org/1999/xlink"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"


def _package(path: Path, mimetype: str, content: str) -> None:
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


def _content(body: str, *, presentation: bool = False) -> str:
    extra = (
        f' xmlns:draw="{DRAW}" xmlns:presentation="{PRESENTATION}" xmlns:xlink="{XLINK}"'
        if presentation
        else ""
    )
    return (
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}"{extra} office:version="1.2">'
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
    assert right.metadata["processor_version"] == "4"


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
