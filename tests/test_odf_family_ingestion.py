from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

from archiv.ingestion.formats import UnsupportedFormatError, media_type_for
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MATHML = "http://www.w3.org/1998/Math/MathML"


def _manifest(mimetype: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<manifest:manifest xmlns:manifest="{MANIFEST}">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )


def _package(path: Path, mimetype: str, content: str) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mimetype, compress_type=ZIP_STORED)
        archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
        archive.writestr(
            "META-INF/manifest.xml",
            _manifest(mimetype),
            compress_type=ZIP_DEFLATED,
        )


def _package_document(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}" xmlns:draw="{DRAW}">'
        f"<office:body>{body}</office:body></office:document-content>"
    )


def _flat_document(mimetype: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document xmlns:office="{OFFICE}" xmlns:text="{TEXT}" '
        f'xmlns:table="{TABLE}" xmlns:draw="{DRAW}" office:mimetype="{mimetype}">'
        f"<office:body>{body}</office:body></office:document>"
    )


def _body(family: str, value: str) -> str:
    if family == "text":
        return f"<office:text><text:p>{value}</text:p></office:text>"
    if family == "spreadsheet":
        return (
            '<office:spreadsheet><table:table table:name="Sheet">'
            "<table:table-row><table:table-cell>"
            f"<text:p>{value}</text:p>"
            "</table:table-cell></table:table-row></table:table></office:spreadsheet>"
        )
    page_container = "presentation" if family == "presentation" else "drawing"
    return (
        f"<office:{page_container}><draw:page><draw:frame><draw:text-box>"
        f"<text:p>{value}</text:p>"
        f"</draw:text-box></draw:frame></draw:page></office:{page_container}>"
    )


@pytest.mark.parametrize(
    ("suffix", "mimetype", "family"),
    [
        ("ott", "application/vnd.oasis.opendocument.text-template", "text"),
        ("odm", "application/vnd.oasis.opendocument.text-master", "text"),
        ("otm", "application/vnd.oasis.opendocument.text-master-template", "text"),
        ("ots", "application/vnd.oasis.opendocument.spreadsheet-template", "spreadsheet"),
        ("otp", "application/vnd.oasis.opendocument.presentation-template", "presentation"),
        ("otg", "application/vnd.oasis.opendocument.graphics-template", "drawing"),
    ],
)
def test_template_and_master_packages_use_exact_media_types(
    tmp_path: Path,
    suffix: str,
    mimetype: str,
    family: str,
) -> None:
    path = tmp_path / f"sample.{suffix}"
    _package(path, mimetype, _package_document(_body(family, "Evidence")))

    result = normalize(path, DIGEST)

    assert result.kind == suffix
    assert result.media_type == mimetype
    assert [segment.text for segment in result.segments] == ["Evidence"]
    assert result.metadata["odf_family"] == family
    assert result.metadata["odf_representation"] == "package"
    assert result.metadata["package_manifest_validated"] is True
    assert result.metadata["declared_mimetype"] == mimetype


@pytest.mark.parametrize(
    ("suffix", "mimetype", "family"),
    [
        ("fodt", "application/vnd.oasis.opendocument.text", "text"),
        ("fods", "application/vnd.oasis.opendocument.spreadsheet", "spreadsheet"),
        ("fodp", "application/vnd.oasis.opendocument.presentation", "presentation"),
        ("fodg", "application/vnd.oasis.opendocument.graphics", "drawing"),
    ],
)
def test_flat_xml_variants_validate_internal_mimetype_and_extract(
    tmp_path: Path,
    suffix: str,
    mimetype: str,
    family: str,
) -> None:
    path = tmp_path / f"sample.{suffix}"
    path.write_text(_flat_document(mimetype, _body(family, "Flat evidence")), encoding="utf-8")

    result = normalize(path, DIGEST)

    assert result.kind == suffix
    assert result.media_type == "text/xml"
    assert [segment.text for segment in result.segments] == ["Flat evidence"]
    assert result.metadata["odf_family"] == family
    assert result.metadata["odf_representation"] == "flat-xml"
    assert result.metadata["package_manifest_validated"] is False
    assert result.metadata["document_mimetype"] == mimetype


def test_flat_xml_registry_media_types_are_explicit() -> None:
    assert media_type_for("document.fodt") == "text/xml"
    assert media_type_for("sheet.fods") == "text/xml"
    assert media_type_for("slides.fodp") == "text/xml"
    assert media_type_for("drawing.fodg") == "text/xml"


def test_flat_xml_mimetype_substitution_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "substituted.fodt"
    path.write_text(
        _flat_document(
            "application/vnd.oasis.opendocument.spreadsheet",
            _body("text", "Wrong claim"),
        ),
        encoding="utf-8",
    )

    with pytest.raises(MalformedInputError, match="flat ODF XML mimetype mismatch"):
        normalize(path, DIGEST)


def test_flat_xml_requires_single_document_root(tmp_path: Path) -> None:
    path = tmp_path / "packaged-root.fodt"
    path.write_text(_package_document(_body("text", "Wrong root")), encoding="utf-8")

    with pytest.raises(MalformedInputError, match="flat ODF XML has an unexpected root"):
        normalize(path, DIGEST)


def test_formula_package_preserves_mathml_source_without_execution(tmp_path: Path) -> None:
    path = tmp_path / "equation.odf"
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<math:math xmlns:math="{MATHML}"><math:mrow>'
        "<math:mi>x</math:mi><math:mo>+</math:mo><math:mn>1</math:mn>"
        "</math:mrow></math:math>"
    )
    _package(path, "application/vnd.oasis.opendocument.formula", content)

    result = normalize(path, DIGEST)

    assert result.kind == "odf"
    assert result.segments[0].locator == {"formula": 1, "representation": "mathml"}
    assert result.segments[0].text == "x+1"
    assert result.metadata["odf_family"] == "formula"
    assert result.metadata["formula_source_kind"] == "mathml"
    formula_source = result.metadata["formula_source"]
    formula_source_sha256 = result.metadata["formula_source_sha256"]
    assert isinstance(formula_source, str)
    assert isinstance(formula_source_sha256, str)
    assert "math" in formula_source
    assert len(formula_source_sha256) == 64
    assert result.metadata["formulas_executed"] is False


def test_formula_package_rejects_non_mathml_content_root(tmp_path: Path) -> None:
    path = tmp_path / "substituted.odf"
    _package(
        path,
        "application/vnd.oasis.opendocument.formula",
        _package_document(_body("text", "Not a formula")),
    )

    with pytest.raises(MalformedInputError, match="must have a MathML math root"):
        normalize(path, DIGEST)


def test_native_inpage_remains_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.inp"
    path.write_bytes(b"not accepted")

    with pytest.raises(UnsupportedFormatError, match="unsupported file type"):
        normalize(path, DIGEST)
