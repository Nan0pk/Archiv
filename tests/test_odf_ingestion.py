from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

import archiv.ingestion.normalize_odf as odf
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"


def _manifest(mimetype: str, *, encrypted: bool = False) -> str:
    encryption = "<manifest:encryption-data/>" if encrypted else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<manifest:manifest xmlns:manifest="{MANIFEST}">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml">'
        f"{encryption}</manifest:file-entry></manifest:manifest>"
    )


def _package(
    path: Path,
    mimetype: str,
    content: str,
    *,
    extra_name: str | None = None,
    encrypted: bool = False,
    mimetype_first: bool = True,
    mimetype_compression: int = ZIP_STORED,
    mimetype_bytes: bytes | None = None,
    manifest_mimetype: str | None = None,
) -> None:
    payload = mimetype.encode("ascii") if mimetype_bytes is None else mimetype_bytes
    manifest = _manifest(manifest_mimetype or mimetype, encrypted=encrypted)
    with ZipFile(path, "w") as archive:
        if not mimetype_first:
            archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
        archive.writestr("mimetype", payload, compress_type=mimetype_compression)
        if mimetype_first:
            archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", manifest, compress_type=ZIP_DEFLATED)
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
    assert result.metadata["package_manifest_validated"] is True
    assert result.metadata["macros_executed"] is False


def test_odt_preserves_odf_spaces_tabs_and_line_breaks(tmp_path: Path) -> None:
    path = tmp_path / "spacing.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document(
            '<office:text><text:p>Urdu<text:s text:c="2"/>English'
            "<text:tab/>42<text:line-break/>next</text:p></office:text>"
        ),
    )

    result = normalize(path, DIGEST)

    assert result.segments[0].text == "Urdu  English\t42\nnext"


def test_ods_records_sheet_cells_formulas_and_table(tmp_path: Path) -> None:
    path = tmp_path / "budget.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _document(
            '<office:spreadsheet><table:table table:name="Budget">'
            "<table:table-row>"
            "<table:table-cell><text:p>Item</text:p></table:table-cell>"
            '<table:table-cell table:formula="of:=SUM([.C2:.C3])" office:value="12" '
            f'xmlns:office="{OFFICE}"/>'
            "</table:table-row></table:table></office:spreadsheet>"
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


def test_ods_expands_bounded_row_and_column_repeats(tmp_path: Path) -> None:
    path = tmp_path / "repeats.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _document(
            '<office:spreadsheet><table:table table:name="Repeated">'
            '<table:table-row table:number-rows-repeated="2">'
            '<table:table-cell table:number-columns-repeated="2"><text:p>X</text:p>'
            "</table:table-cell></table:table-row></table:table></office:spreadsheet>"
        ),
    )

    result = normalize(path, DIGEST)

    assert [segment.locator for segment in result.segments] == [
        {"sheet": "Repeated", "row": 1, "column": 1},
        {"sheet": "Repeated", "row": 1, "column": 2},
        {"sheet": "Repeated", "row": 2, "column": 1},
        {"sheet": "Repeated", "row": 2, "column": 2},
    ]
    assert result.tables[0].rows == [["X", "X"], ["X", "X"]]


def test_ods_rejects_invalid_repeat_values(tmp_path: Path) -> None:
    path = tmp_path / "invalid-repeat.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _document(
            "<office:spreadsheet><table:table><table:table-row>"
            '<table:table-cell table:number-columns-repeated="-1"><text:p>X</text:p>'
            "</table:table-cell></table:table-row></table:table></office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="must be a positive integer"):
        normalize(path, DIGEST)


def test_ods_rejects_aggregate_repeat_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(odf, "MAX_EXPANDED_CELLS", 3)
    path = tmp_path / "aggregate-repeat.ods"
    _package(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        _document(
            "<office:spreadsheet><table:table>"
            '<table:table-row table:number-rows-repeated="2">'
            '<table:table-cell table:number-columns-repeated="2"><text:p>X</text:p>'
            "</table:table-cell></table:table-row></table:table></office:spreadsheet>"
        ),
    )

    with pytest.raises(MalformedInputError, match="expanded cell limit exceeded"):
        normalize(path, DIGEST)


@pytest.mark.parametrize(
    ("suffix", "mimetype", "container", "page_key"),
    [
        ("odp", "application/vnd.oasis.opendocument.presentation", "presentation", "slide"),
        ("odg", "application/vnd.oasis.opendocument.graphics", "drawing", "page"),
    ],
)
def test_odf_pages_have_object_locators_without_splitting_one_object(
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
            "<text:p>First</text:p><text:p>Second</text:p>"
            f"</draw:text-box></draw:frame></draw:page></office:{container}>"
        ),
    )

    result = normalize(path, DIGEST)

    assert len(result.segments) == 1
    assert result.segments[0].locator == {page_key: 1, "object": 1}
    assert result.segments[0].text == "First\nSecond"


def test_odf_mimetype_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "wrong.odt"
    _package(path, "application/vnd.oasis.opendocument.spreadsheet", _document("<office:text/>"))

    with pytest.raises(MalformedInputError, match="mimetype mismatch"):
        normalize(path, DIGEST)


def test_odf_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "manifest-mismatch.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document("<office:text/>"),
        manifest_mimetype="application/vnd.oasis.opendocument.spreadsheet",
    )

    with pytest.raises(MalformedInputError, match="manifest package media type mismatch"):
        normalize(path, DIGEST)


def test_odf_body_must_match_claimed_subtype(tmp_path: Path) -> None:
    path = tmp_path / "substituted.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document("<office:spreadsheet/>"),
    )

    with pytest.raises(MalformedInputError, match="body does not match odt"):
        normalize(path, DIGEST)


def test_odf_rejects_xml_entities_beyond_prefix_scan(tmp_path: Path) -> None:
    path = tmp_path / "entity.odt"
    document = _document("<office:text><text:p>&unsafe;</text:p></office:text>")
    declaration, root = document.split("?>", 1)
    content = (
        declaration
        + "?>"
        + (" " * 9000)
        + '<!DOCTYPE x [<!ENTITY unsafe "expanded">]>'
        + root
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


@pytest.mark.parametrize(
    ("mimetype_first", "compression", "payload", "message"),
    [
        (False, ZIP_STORED, None, "must be the first archive member"),
        (True, ZIP_DEFLATED, None, "stored without compression"),
        (True, ZIP_STORED, b"application/vnd.oasis.opendocument.text\n", "mimetype mismatch"),
    ],
)
def test_odf_enforces_exact_mimetype_storage_rules(
    tmp_path: Path,
    mimetype_first: bool,
    compression: int,
    payload: bytes | None,
    message: str,
) -> None:
    path = tmp_path / "mimetype-rules.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document("<office:text/>"),
        mimetype_first=mimetype_first,
        mimetype_compression=compression,
        mimetype_bytes=payload,
    )

    with pytest.raises(MalformedInputError, match=message):
        normalize(path, DIGEST)


def test_odf_rejects_encrypted_manifest(tmp_path: Path) -> None:
    path = tmp_path / "encrypted.odt"
    _package(
        path,
        "application/vnd.oasis.opendocument.text",
        _document("<office:text/>"),
        encrypted=True,
    )

    with pytest.raises(MalformedInputError, match="encrypted ODF packages"):
        normalize(path, DIGEST)
