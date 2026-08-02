from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"


def _package(path: Path, mimetype: str, content: str, *, extra_name: str | None = None) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mimetype, compress_type=ZIP_STORED)
        archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
        if extra_name is not None:
            archive.writestr(extra_name, "unsafe", compress_type=ZIP_DEFLATED)


def _document(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}" xmlns:draw="{DRAW}">'
        f"<office:body>{body}</office:body></office:document-content>"
    )


def test_odt_preserves_urdu_and_heading_locators(tmp_path: Path) -> None:
    path = tmp_path / "urdu.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document(
            '<office:text><text:h text:outline-level="1">عنوان</text:h>'
            "<text:p>یہ اردو متن ہے۔ English 42</text:p></office:text>"
        ),
    )

    result = normalize(path, DIGEST)

    assert result.kind == "odt"
    assert [segment.text for segment in result.segments] == [
        "عنوان",
        "یہ اردو متن ہے۔ English 42",
    ]
    assert result.segments[0].locator == {"heading": 1, "level": 1}
    assert result.segments[1].locator == {"paragraph": 1}
    assert result.metadata["macros_executed"] is False


def test_ods_records_sheet_cells_formulas_and_table(tmp_path: Path) -> None:
    path = tmp_path / "budget.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _document(
            '<office:spreadsheet><table:table table:name="Budget">'
            "<table:table-row>"
            "<table:table-cell><text:p>Item</text:p></table:table-cell>"
            '<table:table-cell table:formula="of:=SUM([.C2:.C3])"><text:p>12</text:p>'
            "</table:table-cell></table:table-row>"
            "</table:table></office:spreadsheet>"
        ),
    )

    result = normalize(path, DIGEST)

    assert [segment.text for segment in result.segments] == ["Item", "12"]
    assert result.segments[1].locator == {
        "sheet": "Budget",
        "row": 1,
        "column": 2,
        "formula": "of:=SUM([.C2:.C3])",
    }
    assert result.tables[0].locator == {"sheet": "Budget"}
    assert result.tables[0].rows == [["Item", "12"]]
    assert result.metadata["formulas_executed"] is False


@pytest.mark.parametrize(
    ("suffix", "mimetype", "container", "page_key"),
    [
        ("odp", "application/vnd.oasis.opendocument.presentation", "presentation", "slide"),
        ("odg", "application/vnd.oasis.opendocument.graphics", "drawing", "page"),
    ],
)
def test_odf_pages_have_bounded_object_locators(
    tmp_path: Path,
    suffix: str,
    mimetype: str,
    container: str,
    page_key: str,
) -> None:
    path = tmp_path / f"pages.{suffix}"
    _package(
        path,
        mimetype,
        _document(
            f"<office:{container}><draw:page><draw:frame><draw:text-box>"
            f"<text:p>Page text</text:p></draw:text-box></draw:frame></draw:page>"
            f"</office:{container}>"
        ),
    )

    result = normalize(path, DIGEST)

    assert result.segments[0].locator == {page_key: 1, "object": 1}
    assert result.segments[0].text == "Page text"


def test_odf_mimetype_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "wrong.odt"
    _package(path, "application/vnd.oasis.opendocument.spreadsheet", _document("<office:text/>"))

    with pytest.raises(MalformedInputError, match="mimetype mismatch"):
        normalize(path, DIGEST)


def test_odf_rejects_xml_entities(tmp_path: Path) -> None:
    path = tmp_path / "entity.odt"
    content = '<!DOCTYPE x [<!ENTITY unsafe "expanded">]>' + _document(
        "<office:text><text:p>&unsafe;</text:p></office:text>"
    )
    _package(path, "application/vnd.oasis.opendocument.text", content)

    with pytest.raises(MalformedInputError, match="entities are not allowed"):
        normalize(path, DIGEST)


def test_odf_rejects_archive_traversal(tmp_path: Path) -> None:
    path = tmp_path / "traversal.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document("<office:text/>"),
        extra_name="../escape",
    )

    with pytest.raises(MalformedInputError, match="unsafe member path"):
        normalize(path, DIGEST)
