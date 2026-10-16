"""Generated XML fixtures exercise declaration rejection through real readers."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.normalizers import MalformedInputError, normalize
from archiv.research.inpage_legacy import parse_mapping_xml
from archiv.research.inpage_types import ExtractionError, ExtractionLimits
from archiv.storage.layout import ArchivLayout

ENCODINGS = ("utf-8", "utf-16", "utf-16-le", "utf-16-be")
DECLARATIONS = (
    '<!DOCTYPE root [<!ENTITY marker "1575">]>',
    '<!DOCTYPE root [<!ENTITY first "15"><!ENTITY marker "&first;75">]>',
    '<!DOCTYPE root SYSTEM "https://example.invalid/never-fetch.dtd">',
    "<!DOCTYPE root>",
)
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MIMETYPE = "application/vnd.oasis.opendocument.text"


def _encode(body: str, encoding: str, declaration: str = "") -> bytes:
    xml_encoding = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
    return (f'<?xml version="1.0" encoding="{xml_encoding}"?>{declaration}{body}').encode(encoding)


def _odt(path: Path, *, encoding: str, member: str, declaration: str = "") -> None:
    content = (
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:text="{TEXT}">'
        "<office:body><office:text><text:p>اردو &amp; ARCHIV_XML_MARKER</text:p>"
        "</office:text></office:body></office:document-content>"
    )
    manifest = (
        f'<manifest:manifest xmlns:manifest="{MANIFEST}">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{MIMETYPE}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        archive.writestr("mimetype", MIMETYPE)
        for name, body in (("content.xml", content), ("META-INF/manifest.xml", manifest)):
            active_declaration = declaration if name == member else ""
            if active_declaration and "<!ENTITY marker" in active_declaration:
                body = body.replace("ARCHIV_XML_MARKER", "&marker;")
            archive.writestr(name, _encode(body, encoding, active_declaration))


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("member", ("content.xml", "META-INF/manifest.xml"))
@pytest.mark.parametrize("declaration", DECLARATIONS)
def test_odf_declarations_are_refused_before_storage(
    tmp_path: Path, encoding: str, member: str, declaration: str
) -> None:
    source = tmp_path / "declaration.odt"
    _odt(source, encoding=encoding, member=member, declaration=declaration)
    digest = sha256_file(source)
    home = tmp_path / "archive"

    with pytest.raises(MalformedInputError, match="declarations and entities"):
        ingest_file(source, home=home)

    assert sha256_file(source) == digest
    layout = ArchivLayout.resolve(home)
    assert not layout.original_path(digest).exists()
    assert not layout.derived_root(digest).exists()


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_odf_without_declarations_preserves_unicode_and_predefined_entities(
    tmp_path: Path, encoding: str
) -> None:
    source = tmp_path / "ordinary.odt"
    _odt(source, encoding=encoding, member="content.xml")
    result = normalize(source, sha256_file(source))
    assert [segment.text for segment in result.segments] == ["اردو & ARCHIV_XML_MARKER"]


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("declaration", DECLARATIONS)
def test_mapping_declarations_are_refused(encoding: str, declaration: str) -> None:
    value = "&marker;" if "<!ENTITY marker" in declaration else "1575"
    body = f"<root><row><InpageDec>129</InpageDec><UnicodeDec>{value}</UnicodeDec></row></root>"
    with pytest.raises(ExtractionError, match="declarations and entities are forbidden"):
        parse_mapping_xml(_encode(body, encoding, declaration))


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_mapping_without_declarations_preserves_first_key_wins(encoding: str) -> None:
    body = (
        "<root><row><InpageDec>129</InpageDec><UnicodeDec>1575</UnicodeDec></row>"
        "<row><InpageDec>129</InpageDec><UnicodeDec>1576</UnicodeDec></row>"
        "<note>اردو &amp; text</note></root>"
    )
    mapping = parse_mapping_xml(_encode(body, encoding))
    assert mapping.values == {129: "ا"}
    assert mapping.duplicates == 1
    assert mapping.conflicts == 1


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_mapping_malformed_xml_and_size_limits_still_refuse(encoding: str) -> None:
    with pytest.raises(ExtractionError, match="mapping XML is malformed"):
        parse_mapping_xml(_encode("<root><row></root>", encoding))
    with pytest.raises(ExtractionError, match="mapping file size limit exceeded"):
        parse_mapping_xml(
            _encode("<root/>", encoding), limits=ExtractionLimits(max_mapping_bytes=4)
        )


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_xml_declaration_text_in_comments_and_cdata_is_not_a_declaration(encoding: str) -> None:
    body = (
        '<root><!-- <!DOCTYPE root [<!ENTITY marker "1575">]> -->'
        "<note><![CDATA[<!ENTITY marker '1575'>]]></note>"
        "<row><InpageDec>129</InpageDec><UnicodeDec>1575</UnicodeDec></row></root>"
    )
    assert parse_mapping_xml(_encode(body, encoding)).values == {129: "ا"}
