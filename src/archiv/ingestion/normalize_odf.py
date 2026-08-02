"""Bounded, non-executing normalization for core OpenDocument packages."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable

ODF_MIMETYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odg": "application/vnd.oasis.opendocument.graphics",
}

MAX_ARCHIVE_ENTRIES = 4096
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_XML_NODES = 250_000

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
XLINK = "http://www.w3.org/1999/xlink"


@dataclass(frozen=True)
class _Package:
    mimetype: str
    content: bytes


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element) -> str:
    return "".join(element.itertext()).strip()


def _parse_xml(data: bytes) -> ElementTree.Element:
    upper = data[:8192].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("ODF XML declarations and entities are not allowed")
    root = ElementTree.fromstring(data)
    if sum(1 for _ in root.iter()) > MAX_XML_NODES:
        raise ValueError("ODF XML node limit exceeded")
    return root


def _read_package(path: Path, expected_mimetype: str) -> _Package:
    try:
        with ZipFile(BytesIO(path.read_bytes())) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("ODF archive entry limit exceeded")
            total = 0
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                    raise ValueError("ODF archive contains an unsafe member path")
                if name in names:
                    raise ValueError("ODF archive contains duplicate member names")
                names.add(name)
                if entry.file_size > MAX_MEMBER_BYTES:
                    raise ValueError("ODF archive member size limit exceeded")
                total += entry.file_size
                if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValueError("ODF archive total size limit exceeded")
                if entry.compress_size == 0 and entry.file_size:
                    raise ValueError("ODF archive has an invalid compression ratio")
                if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
                    raise ValueError("ODF archive compression-ratio limit exceeded")
            if "mimetype" not in names or "content.xml" not in names:
                raise ValueError("ODF package is missing required members")
            mimetype = archive.read("mimetype").decode("ascii", errors="strict").strip()
            if mimetype != expected_mimetype:
                raise ValueError(f"ODF package mimetype mismatch: {mimetype!r}")
            content = archive.read("content.xml")
    except BadZipFile as error:
        raise ValueError("invalid ODF ZIP package") from error
    return _Package(mimetype=mimetype, content=content)


def _paragraph_segments(root: ElementTree.Element) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    paragraph = 0
    heading = 0
    for element in root.iter():
        name = _local_name(element.tag)
        if name not in {"p", "h"}:
            continue
        value = _text(element)
        if not value:
            continue
        if name == "h":
            heading += 1
            locator: dict[str, object] = {"heading": heading}
            level = element.attrib.get(f"{{{TEXT}}}outline-level")
            if level and level.isdigit():
                locator["level"] = int(level)
        else:
            paragraph += 1
            locator = {"paragraph": paragraph}
        segments.append(NormalizedSegment(locator=locator, text=value))
    return segments


def _spreadsheet(root: ElementTree.Element) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    for sheet_number, sheet in enumerate(root.iter(f"{{{TABLE}}}table"), 1):
        sheet_name = sheet.attrib.get(f"{{{TABLE}}}name") or f"Sheet {sheet_number}"
        rows: list[list[object | None]] = []
        for row_number, row in enumerate(sheet.findall(f"{{{TABLE}}}table-row"), 1):
            values: list[object | None] = []
            column = 0
            for cell in row:
                if _local_name(cell.tag) not in {"table-cell", "covered-table-cell"}:
                    continue
                repeat_text = cell.attrib.get(f"{{{TABLE}}}number-columns-repeated", "1")
                repeat = int(repeat_text) if repeat_text.isdigit() else 1
                if repeat > 10_000:
                    raise ValueError("ODF repeated-cell limit exceeded")
                value = _text(cell) or None
                formula = cell.attrib.get(f"{{{TABLE}}}formula")
                for _ in range(repeat):
                    column += 1
                    values.append(value)
                    if value is not None or formula is not None:
                        locator: dict[str, object] = {
                            "sheet": sheet_name,
                            "row": row_number,
                            "column": column,
                        }
                        if formula is not None:
                            locator["formula"] = formula
                        segments.append(
                            NormalizedSegment(
                                locator=locator,
                                text=value if value is not None else formula or "",
                            )
                        )
            if any(value is not None for value in values):
                rows.append(values)
        if rows:
            tables.append(NormalizedTable(locator={"sheet": sheet_name}, rows=rows))
    return segments, tables


def _page_segments(root: ElementTree.Element, *, page_key: str) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    page_tag = f"{{{DRAW}}}page"
    for page_number, page in enumerate(root.iter(page_tag), 1):
        object_number = 0
        for element in page.iter():
            if _local_name(element.tag) not in {"p", "h"}:
                continue
            value = _text(element)
            if not value:
                continue
            object_number += 1
            segments.append(
                NormalizedSegment(
                    locator={page_key: page_number, "object": object_number},
                    text=value,
                )
            )
    return segments


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
    root = _parse_xml(package.content)
    body = root.find(f"{{{OFFICE}}}body")
    if body is None:
        raise ValueError("ODF content.xml has no office:body")

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
        if href and ":" in href.split("/", 1)[0]:
            external_links += 1

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind=kind,
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata={
            "processor": "archiv.odf-core",
            "processor_version": "1",
            "package_mimetype": package.mimetype,
            "external_links_ignored": external_links,
            "macros_executed": False,
            "formulas_executed": False,
        },
    )
