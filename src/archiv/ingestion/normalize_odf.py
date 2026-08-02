"""Bounded, non-executing normalization for core OpenDocument packages."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable

ODF_MIMETYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odg": "application/vnd.oasis.opendocument.graphics",
}
ODF_BODY_TAGS = {
    "odt": "text",
    "ods": "spreadsheet",
    "odp": "presentation",
    "odg": "drawing",
}

MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_XML_NODES = 250_000
MAX_SEGMENT_CHARACTERS = 2 * 1024 * 1024
MAX_TEXT_SPACE_REPEAT = 10_000
MAX_COLUMNS_PER_ROW = 16_384
MAX_EXPANDED_ROWS = 10_000
MAX_EXPANDED_CELLS = 50_000

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
XLINK = "http://www.w3.org/1999/xlink"


@dataclass(frozen=True)
class _Package:
    mimetype: str
    content: bytes
    manifest: bytes


@dataclass(frozen=True)
class _CellSpec:
    repeat: int
    value: str | None
    formula: str | None


def _positive_integer(value: str | None, *, label: str, maximum: int) -> int:
    if value is None:
        return 1
    if not value or any(character not in "0123456789" for character in value):
        raise ValueError(f"ODF {label} must be a positive integer")
    result = int(value)
    if result < 1:
        raise ValueError(f"ODF {label} must be a positive integer")
    if result > maximum:
        raise ValueError(f"ODF {label} limit exceeded")
    return result


def _collect_text(element: ElementTree.Element, parts: list[str]) -> None:
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tag == f"{{{TEXT}}}s":
            repeat = _positive_integer(
                child.attrib.get(f"{{{TEXT}}}c"),
                label="space repeat",
                maximum=MAX_TEXT_SPACE_REPEAT,
            )
            parts.append(" " * repeat)
        elif child.tag == f"{{{TEXT}}}tab":
            parts.append("\t")
        elif child.tag == f"{{{TEXT}}}line-break":
            parts.append("\n")
        else:
            _collect_text(child, parts)
        if child.tail:
            parts.append(child.tail)


def _odf_text(element: ElementTree.Element) -> str:
    parts: list[str] = []
    _collect_text(element, parts)
    value = "".join(parts).strip()
    if len(value) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    return value


def _parse_xml(data: bytes, *, label: str) -> ElementTree.Element:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError(f"ODF {label} declarations and entities are not allowed")
    root = ElementTree.fromstring(data)
    if sum(1 for _ in root.iter()) > MAX_XML_NODES:
        raise ValueError(f"ODF {label} node limit exceeded")
    return root


def _validate_manifest(data: bytes, expected_mimetype: str) -> None:
    root = _parse_xml(data, label="manifest XML")
    if root.tag != f"{{{MANIFEST}}}manifest":
        raise ValueError("ODF manifest has an unexpected root element")
    if root.find(f".//{{{MANIFEST}}}encryption-data") is not None:
        raise ValueError("encrypted ODF packages are not supported")

    file_entries = list(root.iter(f"{{{MANIFEST}}}file-entry"))
    root_entries = [
        entry for entry in file_entries if entry.attrib.get(f"{{{MANIFEST}}}full-path") == "/"
    ]
    if len(root_entries) != 1:
        raise ValueError("ODF manifest must declare exactly one package root")
    if root_entries[0].attrib.get(f"{{{MANIFEST}}}media-type") != expected_mimetype:
        raise ValueError("ODF manifest package media type mismatch")

    content_entries = [
        entry
        for entry in file_entries
        if entry.attrib.get(f"{{{MANIFEST}}}full-path") == "content.xml"
    ]
    if len(content_entries) != 1:
        raise ValueError("ODF manifest must declare content.xml exactly once")
    if content_entries[0].attrib.get(f"{{{MANIFEST}}}media-type") != "text/xml":
        raise ValueError("ODF manifest content.xml media type mismatch")


def _read_package(path: Path, expected_mimetype: str) -> _Package:
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("ODF package size limit exceeded")
    try:
        with ZipFile(BytesIO(path.read_bytes())) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("ODF archive entry limit exceeded")
            if not entries or entries[0].filename != "mimetype":
                raise ValueError("ODF mimetype must be the first archive member")
            if entries[0].compress_type != ZIP_STORED or entries[0].extra:
                raise ValueError("ODF mimetype must be stored without compression or extra fields")

            total = 0
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise ValueError("ODF archive contains an unsafe member path")
                if name in names:
                    raise ValueError("ODF archive contains duplicate member names")
                names.add(name)
                if entry.flag_bits & 0x1:
                    raise ValueError("encrypted ODF archive members are not supported")
                if entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise ValueError("ODF archive uses an unsupported compression method")
                if entry.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("ODF archive member size limit exceeded")
                total += entry.file_size
                if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValueError("ODF archive total size limit exceeded")
                if entry.compress_size == 0 and entry.file_size:
                    raise ValueError("ODF archive has an invalid compression ratio")
                if (
                    entry.compress_size
                    and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("ODF archive compression-ratio limit exceeded")

            required = {"mimetype", "content.xml", "META-INF/manifest.xml"}
            if not required.issubset(names):
                raise ValueError("ODF package is missing required members")
            mimetype = archive.read("mimetype").decode("ascii", errors="strict")
            if mimetype != expected_mimetype:
                raise ValueError(f"ODF package mimetype mismatch: {mimetype!r}")
            content = archive.read("content.xml")
            manifest = archive.read("META-INF/manifest.xml")
    except BadZipFile as error:
        raise ValueError("invalid ODF ZIP package") from error

    _validate_manifest(manifest, expected_mimetype)
    return _Package(mimetype=mimetype, content=content, manifest=manifest)


def _paragraph_segments(root: ElementTree.Element) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    paragraph = 0
    heading = 0
    for element in root.iter():
        if element.tag not in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            continue
        value = _odf_text(element)
        if not value:
            continue
        if element.tag == f"{{{TEXT}}}h":
            heading += 1
            locator: dict[str, object] = {"heading": heading}
            level = element.attrib.get(f"{{{TEXT}}}outline-level")
            if level is not None:
                locator["level"] = _positive_integer(
                    level,
                    label="heading level",
                    maximum=10,
                )
        else:
            paragraph += 1
            locator = {"paragraph": paragraph}
        segments.append(NormalizedSegment(locator=locator, text=value))
    return segments


def _iter_sheets(element: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag == f"{{{TABLE}}}table":
            yield child
        else:
            yield from _iter_sheets(child)


def _iter_sheet_rows(element: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag == f"{{{TABLE}}}table-row":
            yield child
        elif child.tag == f"{{{TABLE}}}table":
            continue
        else:
            yield from _iter_sheet_rows(child)


def _cell_value(cell: ElementTree.Element) -> str | None:
    displayed = _odf_text(cell)
    if displayed:
        return displayed
    for attribute in (
        "string-value",
        "value",
        "date-value",
        "time-value",
        "boolean-value",
        "currency",
    ):
        value = cell.attrib.get(f"{{{OFFICE}}}{attribute}")
        if value is not None:
            return value
    return None


def _row_template(row: ElementTree.Element) -> tuple[list[_CellSpec], int]:
    specs: list[_CellSpec] = []
    width = 0
    for cell in row:
        if cell.tag not in {
            f"{{{TABLE}}}table-cell",
            f"{{{TABLE}}}covered-table-cell",
        }:
            continue
        repeat = _positive_integer(
            cell.attrib.get(f"{{{TABLE}}}number-columns-repeated"),
            label="column repeat",
            maximum=MAX_COLUMNS_PER_ROW,
        )
        width += repeat
        if width > MAX_COLUMNS_PER_ROW:
            raise ValueError("ODF spreadsheet column limit exceeded")
        specs.append(
            _CellSpec(
                repeat=repeat,
                value=_cell_value(cell),
                formula=cell.attrib.get(f"{{{TABLE}}}formula"),
            )
        )
    return specs, width


def _spreadsheet(
    root: ElementTree.Element,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    expanded_cells = 0
    expanded_rows = 0
    for sheet_number, sheet in enumerate(_iter_sheets(root), 1):
        sheet_name = sheet.attrib.get(f"{{{TABLE}}}name") or f"Sheet {sheet_number}"
        rows: list[list[object | None]] = []
        row_number = 0
        for row in _iter_sheet_rows(sheet):
            row_repeat = _positive_integer(
                row.attrib.get(f"{{{TABLE}}}number-rows-repeated"),
                label="row repeat",
                maximum=MAX_EXPANDED_ROWS,
            )
            specs, row_width = _row_template(row)
            if expanded_rows + row_repeat > MAX_EXPANDED_ROWS:
                raise ValueError("ODF expanded row limit exceeded")
            if expanded_cells + row_width * row_repeat > MAX_EXPANDED_CELLS:
                raise ValueError("ODF expanded cell limit exceeded")
            expanded_rows += row_repeat
            expanded_cells += row_width * row_repeat

            for _ in range(row_repeat):
                row_number += 1
                values: list[object | None] = []
                column = 0
                for spec in specs:
                    for _ in range(spec.repeat):
                        column += 1
                        values.append(spec.value)
                        if spec.value is not None or spec.formula is not None:
                            locator: dict[str, object] = {
                                "sheet": sheet_name,
                                "row": row_number,
                                "column": column,
                            }
                            if spec.formula is not None:
                                locator["formula"] = spec.formula
                            segments.append(
                                NormalizedSegment(
                                    locator=locator,
                                    text=(
                                        spec.value if spec.value is not None else spec.formula or ""
                                    ),
                                )
                            )
                if any(value is not None for value in values):
                    rows.append(values)
        if rows:
            tables.append(NormalizedTable(locator={"sheet": sheet_name}, rows=rows))
    return segments, tables


def _iter_pages(element: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag == f"{{{DRAW}}}page":
            yield child
        else:
            yield from _iter_pages(child)


def _object_text(element: ElementTree.Element) -> str:
    paragraphs = [
        _odf_text(paragraph)
        for paragraph in element.iter()
        if paragraph.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}
    ]
    return "\n".join(value for value in paragraphs if value)


def _page_segments(root: ElementTree.Element, *, page_key: str) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    for page_number, page in enumerate(_iter_pages(root), 1):
        object_number = 0
        for element in page:
            if element.tag == f"{{{PRESENTATION}}}notes":
                continue
            object_number += 1
            value = _object_text(element)
            if not value:
                continue
            segments.append(
                NormalizedSegment(
                    locator={page_key: page_number, "object": object_number},
                    text=value,
                )
            )
    return segments


def _validated_body(root: ElementTree.Element, kind: str) -> ElementTree.Element:
    if root.tag != f"{{{OFFICE}}}document-content":
        raise ValueError("ODF content.xml has an unexpected root element")
    body = root.find(f"{{{OFFICE}}}body")
    if body is None:
        raise ValueError("ODF content.xml has no office:body")
    children = list(body)
    expected = f"{{{OFFICE}}}{ODF_BODY_TAGS[kind]}"
    if len(children) != 1 or children[0].tag != expected:
        raise ValueError(f"ODF content body does not match {kind}")
    return children[0]


def normalize_odf(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    kind: str,
) -> NormalizedDocument:
    """Validate and normalize one core ODF ZIP package without executing content."""

    expected = ODF_MIMETYPES[kind]
    if media_type != expected:
        raise ValueError("ODF registry media type mismatch")
    package = _read_package(path, expected)
    root = _parse_xml(package.content, label="content XML")
    body = _validated_body(root, kind)

    segments: list[NormalizedSegment]
    tables: list[NormalizedTable] = []
    if kind == "ods":
        segments, tables = _spreadsheet(body)
    elif kind == "odp":
        segments = _page_segments(body, page_key="slide")
    elif kind == "odg":
        segments = _page_segments(body, page_key="page")
    else:
        segments = _paragraph_segments(body)

    external_links = 0
    for element in root.iter():
        href = element.attrib.get(f"{{{XLINK}}}href")
        if href and (href.startswith("//") or ":" in href.split("/", 1)[0]):
            external_links += 1

    metadata: dict[str, object] = {
        "processor": "archiv.odf-core",
        "processor_version": "2",
        "package_mimetype": package.mimetype,
        "package_manifest_validated": True,
        "external_links_ignored": external_links,
        "macros_executed": False,
        "formulas_executed": False,
    }
    if kind == "odp":
        metadata["presentation_notes_extracted"] = False

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind=kind,
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata=metadata,
    )
