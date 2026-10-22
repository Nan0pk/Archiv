"""Bounded, non-executing normalization for the supported OpenDocument family."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable
from archiv.xml_safety import MalformedXmlError, XmlDeclarationError, refuse_xml_declarations

ODF_MIMETYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ott": "application/vnd.oasis.opendocument.text-template",
    "odm": "application/vnd.oasis.opendocument.text-master",
    "otm": "application/vnd.oasis.opendocument.text-master-template",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "otp": "application/vnd.oasis.opendocument.presentation-template",
    "odg": "application/vnd.oasis.opendocument.graphics",
    "otg": "application/vnd.oasis.opendocument.graphics-template",
    "odf": "application/vnd.oasis.opendocument.formula",
    "fodt": "application/vnd.oasis.opendocument.text",
    "fods": "application/vnd.oasis.opendocument.spreadsheet",
    "fodp": "application/vnd.oasis.opendocument.presentation",
    "fodg": "application/vnd.oasis.opendocument.graphics",
}
FLAT_XML_KINDS = {"fodt", "fods", "fodp", "fodg"}
ODF_REGISTRY_MEDIA_TYPES = {
    kind: ("text/xml" if kind in FLAT_XML_KINDS else mimetype)
    for kind, mimetype in ODF_MIMETYPES.items()
}
ODF_BODY_TAGS = {
    "odt": "text",
    "ott": "text",
    "odm": "text",
    "otm": "text",
    "fodt": "text",
    "ods": "spreadsheet",
    "ots": "spreadsheet",
    "fods": "spreadsheet",
    "odp": "presentation",
    "otp": "presentation",
    "fodp": "presentation",
    "odg": "drawing",
    "otg": "drawing",
    "fodg": "drawing",
}

MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_FLAT_XML_BYTES = 32 * 1024 * 1024
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
MATHML = "http://www.w3.org/1998/Math/MathML"
XLINK = "http://www.w3.org/1999/xlink"


@dataclass(frozen=True)
class OdfPackage:
    """Validated ODF package members shared by bounded family normalizers."""

    mimetype: str
    content: bytes
    manifest: bytes


@dataclass(frozen=True)
class _CellSpec:
    repeat: int
    value: str | None
    formula: str | None


@dataclass
class _TableBudget:
    """Upper bound on expanded rows and cells across every table in one document."""

    rows: int = 0
    cells: int = 0

    def spend(self, rows: int, columns: int, *, label: str) -> None:
        if self.rows + rows > MAX_EXPANDED_ROWS:
            raise ValueError(f"ODF {label} expanded row limit exceeded")
        if self.cells + rows * columns > MAX_EXPANDED_CELLS:
            raise ValueError(f"ODF {label} expanded cell limit exceeded")
        self.rows += rows
        self.cells += rows * columns


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


def parse_odf_xml(data: bytes, *, label: str) -> ElementTree.Element:
    """Parse bounded ODF XML while rejecting declarations, entities, and node floods."""

    try:
        refuse_xml_declarations(data)
    except XmlDeclarationError:
        raise ValueError(f"ODF {label} declarations and entities are not allowed") from None
    except MalformedXmlError as error:
        raise ValueError(f"ODF {label} is not well-formed XML") from error
    root = ElementTree.fromstring(data)
    if sum(1 for _ in root.iter()) > MAX_XML_NODES:
        raise ValueError(f"ODF {label} node limit exceeded")
    return root


def _validate_manifest(data: bytes, expected_mimetype: str) -> None:
    root = parse_odf_xml(data, label="manifest XML")
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


def read_odf_package(path: Path, expected_mimetype: str) -> OdfPackage:
    """Validate one bounded ODF ZIP package and return required member bytes."""

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
    return OdfPackage(mimetype=mimetype, content=content, manifest=manifest)


def _read_flat_xml(path: Path) -> bytes:
    if path.stat().st_size > MAX_FLAT_XML_BYTES:
        raise ValueError("flat ODF XML size limit exceeded")
    return path.read_bytes()


def _text_content(
    body: ElementTree.Element,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    """Paragraphs and tables in document order, with no paragraph indexed twice."""

    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    paragraph = 0
    heading = 0
    table_number = 0
    budget = _TableBudget()
    stack = list(reversed(list(body)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table":
            table_number += 1
            table_segments, table = _read_table(
                element,
                {"table": table_number},
                budget,
                label="table",
            )
            segments.extend(table_segments)
            if table is not None:
                tables.append(table)
            continue
        if element.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
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
            continue
        stack.extend(reversed(list(element)))
    return segments, tables


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


def _paragraph_texts(
    element: ElementTree.Element,
    *,
    skip_tables: bool = False,
) -> list[str]:
    """Paragraph text under one container, stopping at each paragraph.

    Stopping at a paragraph keeps a note body from being collected twice: the
    paragraph's own text already includes it inline. A table subtree is skipped only
    when the caller asks for the text outside a frame's table.
    """

    paragraphs: list[str] = []
    stack = list(reversed(list(element)))
    while stack:
        child = stack.pop()
        if skip_tables and child.tag == f"{{{TABLE}}}table":
            continue
        if child.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            value = _odf_text(child)
            if value:
                paragraphs.append(value)
            continue
        stack.extend(reversed(list(child)))
    return paragraphs


def _cell_text(cell: ElementTree.Element) -> str | None:
    """A table cell's paragraphs, joined the way the Word reader joins them."""

    joined = "\n".join(_paragraph_texts(cell))
    if len(joined) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    if joined:
        return joined
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


_ROW_CONTAINERS = {
    f"{{{TABLE}}}table-rows",
    f"{{{TABLE}}}table-header-rows",
    f"{{{TABLE}}}table-row-group",
}


def _table_rows(table: ElementTree.Element) -> Iterator[ElementTree.Element]:
    """Rows in document order, including rows inside a header or group wrapper."""

    stack = list(reversed(list(table)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table-row":
            yield element
        elif element.tag in _ROW_CONTAINERS:
            stack.extend(reversed(list(element)))


def _read_table(
    table: ElementTree.Element,
    prefix: dict[str, object],
    budget: _TableBudget,
    *,
    label: str,
) -> tuple[list[NormalizedSegment], NormalizedTable | None]:
    """One segment per non-empty cell, located by row and column plus the prefix.

    A covered cell carries no text of its own, matching the Word reader's merge
    handling. A table nested inside a cell is refused rather than silently
    flattened into the outer cell.
    """

    if table.find(f".//{{{TABLE}}}table") is not None:
        raise ValueError(f"ODF {label} nests a table inside a cell")
    segments: list[NormalizedSegment] = []
    rows: list[list[object | None]] = []
    for row in _table_rows(table):
        row_repeat = _positive_integer(
            row.attrib.get(f"{{{TABLE}}}number-rows-repeated"),
            label="row repeat",
            maximum=MAX_EXPANDED_ROWS,
        )
        cells: list[tuple[int, str | None]] = []
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
                raise ValueError(f"ODF {label} column limit exceeded")
            text = None if cell.tag == f"{{{TABLE}}}covered-table-cell" else _cell_text(cell)
            cells.append((repeat, text))
        if width == 0:
            continue
        budget.spend(row_repeat, width, label=label)
        for _ in range(row_repeat):
            values: list[object | None] = []
            column = 0
            for repeat, text in cells:
                for _ in range(repeat):
                    column += 1
                    values.append(text)
                    if text is not None:
                        locator: dict[str, object] = {
                            **prefix,
                            "row": len(rows) + 1,
                            "column": column,
                        }
                        segments.append(NormalizedSegment(locator=locator, text=text))
            rows.append(values)
    if not any(value is not None for row in rows for value in row):
        return segments, None
    width = max(len(row) for row in rows)
    padded = [row + [None] * (width - len(row)) for row in rows]
    return segments, NormalizedTable(locator=dict(prefix), rows=padded)


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
    joined = "\n".join(_paragraph_texts(element))
    if len(joined) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    return joined


def _text_outside_tables(element: ElementTree.Element) -> str:
    """A frame's paragraph text that is not inside its table."""

    joined = "\n".join(_paragraph_texts(element, skip_tables=True))
    if len(joined) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    return joined


_NOTES_FURNITURE = {"page", "page-number", "date-time", "header", "footer"}


def _notes_segments(notes: ElementTree.Element, slide: int) -> list[NormalizedSegment]:
    """Speaker notes from top-level text frames, without the page furniture."""

    segments: list[NormalizedSegment] = []
    spoken = 0
    for frame in notes:
        if frame.tag != f"{{{DRAW}}}frame":
            continue
        if frame.attrib.get(f"{{{PRESENTATION}}}class") in _NOTES_FURNITURE:
            continue
        if frame.find(f".//{{{TABLE}}}table") is not None:
            continue
        if frame.find(f".//{{{DRAW}}}g") is not None:
            continue
        value = _object_text(frame)
        if not value:
            continue
        spoken += 1
        segments.append(
            NormalizedSegment(
                locator={"slide": slide, "speaker_notes": spoken},
                text=value,
            )
        )
    return segments


def _page_segments(
    root: ElementTree.Element,
    *,
    page_key: str,
    include_notes: bool,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    budget = _TableBudget()
    for page_number, page in enumerate(_iter_pages(root), 1):
        object_number = 0
        for element in page:
            if element.tag == f"{{{PRESENTATION}}}notes":
                if include_notes:
                    segments.extend(_notes_segments(element, page_number))
                continue
            object_number += 1
            table = element.find(f"{{{TABLE}}}table") if element.tag == f"{{{DRAW}}}frame" else None
            if table is not None:
                table_segments, normalized = _read_table(
                    table,
                    {page_key: page_number, "object": object_number},
                    budget,
                    label="presentation table",
                )
                segments.extend(table_segments)
                if normalized is not None:
                    tables.append(normalized)
                remaining = _text_outside_tables(element)
                if remaining:
                    segments.append(
                        NormalizedSegment(
                            locator={page_key: page_number, "object": object_number},
                            text=remaining,
                        )
                    )
                continue
            value = _object_text(element)
            if not value:
                continue
            segments.append(
                NormalizedSegment(
                    locator={page_key: page_number, "object": object_number},
                    text=value,
                )
            )
    return segments, tables


def _validated_body(
    root: ElementTree.Element,
    kind: str,
    *,
    flat_xml: bool,
) -> ElementTree.Element:
    expected_root = f"{{{OFFICE}}}{'document' if flat_xml else 'document-content'}"
    if root.tag != expected_root:
        label = "flat ODF XML" if flat_xml else "ODF content.xml"
        raise ValueError(f"{label} has an unexpected root element")
    if flat_xml:
        declared = root.attrib.get(f"{{{OFFICE}}}mimetype")
        if declared != ODF_MIMETYPES[kind]:
            raise ValueError("flat ODF XML mimetype mismatch")
    body = root.find(f"{{{OFFICE}}}body")
    if body is None:
        raise ValueError("ODF document has no office:body")
    children = list(body)
    expected = f"{{{OFFICE}}}{ODF_BODY_TAGS[kind]}"
    if len(children) != 1 or children[0].tag != expected:
        raise ValueError(f"ODF content body does not match {kind}")
    return children[0]


def _formula_segments(
    root: ElementTree.Element,
) -> tuple[list[NormalizedSegment], str, str]:
    if root.tag != f"{{{MATHML}}}math":
        raise ValueError("ODF formula content.xml must have a MathML math root")
    source = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
    if len(source) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF formula source character limit exceeded")
    text = _odf_text(root) or source
    return (
        [
            NormalizedSegment(
                locator={"formula": 1, "representation": "mathml"},
                text=text,
            )
        ],
        source,
        sha256(source.encode("utf-8")).hexdigest(),
    )


def normalize_odf(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    kind: str,
) -> NormalizedDocument:
    """Validate and normalize one supported ODF representation without execution."""

    expected_mimetype = ODF_MIMETYPES[kind]
    if media_type != ODF_REGISTRY_MEDIA_TYPES[kind]:
        raise ValueError("ODF registry media type mismatch")

    package: OdfPackage | None = None
    flat_xml = kind in FLAT_XML_KINDS
    if flat_xml:
        content = _read_flat_xml(path)
        representation = "flat-xml"
    else:
        package = read_odf_package(path, expected_mimetype)
        content = package.content
        representation = "package"
    root = parse_odf_xml(content, label="content XML")

    tables: list[NormalizedTable] = []
    formula_source: str | None = None
    formula_source_sha256: str | None = None
    family = ODF_BODY_TAGS.get(kind, "formula")
    if family == "formula":
        segments, formula_source, formula_source_sha256 = _formula_segments(root)
    else:
        body = _validated_body(root, kind, flat_xml=flat_xml)
        if family == "spreadsheet":
            segments, tables = _spreadsheet(body)
        elif family == "presentation":
            segments, tables = _page_segments(
                body,
                page_key="slide",
                include_notes=True,
            )
        elif family == "drawing":
            segments, tables = _page_segments(
                body,
                page_key="page",
                include_notes=False,
            )
        else:
            segments, tables = _text_content(body)

    external_links = 0
    for element in root.iter():
        href = element.attrib.get(f"{{{XLINK}}}href")
        if href and (href.startswith("//") or ":" in href.split("/", 1)[0]):
            external_links += 1

    metadata: dict[str, object] = {
        "processor": "archiv.odf-core",
        "processor_version": "4",
        "odf_family": family,
        "odf_representation": representation,
        "declared_mimetype": expected_mimetype,
        "package_manifest_validated": package is not None,
        "external_links_ignored": external_links,
        "macros_executed": False,
        "formulas_executed": False,
    }
    if package is not None:
        metadata["package_mimetype"] = package.mimetype
    else:
        metadata["document_mimetype"] = expected_mimetype
    if family == "presentation":
        metadata["presentation_notes_extracted"] = any(
            "speaker_notes" in segment.locator for segment in segments
        )
    if formula_source is not None and formula_source_sha256 is not None:
        metadata["formula_source_kind"] = "mathml"
        metadata["formula_source"] = formula_source
        metadata["formula_source_sha256"] = formula_source_sha256

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind=kind,
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata=metadata,
    )
