"""Bounded, non-executing normalization for the supported OpenDocument family."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
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
MAX_GROUP_DEPTH = 32

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
PRESENTATION = "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
CHART = "urn:oasis:names:tc:opendocument:xmlns:chart:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
MATHML = "http://www.w3.org/1998/Math/MathML"
XLINK = "http://www.w3.org/1999/xlink"

_NOTE_CLASSES = {"footnote", "endnote"}
_HIDDEN_VISIBILITY = {"collapse", "filter"}
_FALSE_VALUES = {"false", "0"}
_ROW_CONTAINERS = {
    f"{{{TABLE}}}table-rows",
    f"{{{TABLE}}}table-header-rows",
    f"{{{TABLE}}}table-row-group",
}
_COLUMN_CONTAINERS = {
    f"{{{TABLE}}}table-columns",
    f"{{{TABLE}}}table-header-columns",
    f"{{{TABLE}}}table-column-group",
}
_GROUP_IGNORED = {
    f"{{{DRAW}}}glue-point",
    f"{{{DRAW}}}layer-set",
}
_PLACEHOLDER_ROLES = {
    "title": "title",
    "subtitle": "subtitle",
    "outline": "body",
    "text": "content",
    "object": "content",
    "graphic": "picture",
    "orgchart": "diagram",
    "page": "slide image",
    "page-number": "slide number",
    "date-time": "date",
    "header": "header",
    "footer": "footer",
    "notes": "notes",
    "handout": "handout",
    "chart": "chart",
    "table": "table",
}
_CELL_ADDRESS = re.compile(r"^\$?(?P<table>[^.]+)\.\$?(?P<column>[A-Za-z]+)\$?(?P<row>[0-9]+)$")


@dataclass(frozen=True)
class OdfPackage:
    """Validated ODF package members shared by bounded family normalizers."""

    mimetype: str
    content: bytes
    manifest: bytes
    objects: dict[str, bytes] = field(default_factory=lambda: dict[str, bytes]())


@dataclass(frozen=True)
class _CellSpec:
    repeat: int
    value: str | None
    formula: str | None
    column_span: int = 1
    row_span: int = 1
    links: tuple[str, ...] = ()


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


def _collect_text(
    element: ElementTree.Element, parts: list[str], *, drop_notes: bool = False
) -> None:
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
        elif (
            drop_notes
            and child.tag == f"{{{TEXT}}}note"
            and child.attrib.get(f"{{{TEXT}}}note-class") in _NOTE_CLASSES
        ):
            # In a text document the note is indexed as its own structure after
            # the document body; keeping it inline as well would index it twice.
            pass
        else:
            _collect_text(child, parts, drop_notes=drop_notes)
        if child.tail:
            parts.append(child.tail)


def _odf_text(element: ElementTree.Element, *, drop_notes: bool = False) -> str:
    parts: list[str] = []
    _collect_text(element, parts, drop_notes=drop_notes)
    value = "".join(parts).strip()
    if len(value) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    return value


def _notes_in(element: ElementTree.Element) -> list[ElementTree.Element]:
    """Footnote and endnote elements under ``element``, in document order."""

    return [
        note
        for note in element.iter(f"{{{TEXT}}}note")
        if note.attrib.get(f"{{{TEXT}}}note-class") in _NOTE_CLASSES
    ]


def _link_targets(element: ElementTree.Element) -> list[str]:
    """Inline link targets in document order; an empty target is skipped.

    Notes are their own structure, so a link inside a note body is not also a
    link of the surrounding paragraph or cell.
    """

    targets: list[str] = []
    stack = list(reversed(list(element)))
    while stack:
        child = stack.pop()
        if (
            child.tag == f"{{{TEXT}}}note"
            and child.attrib.get(f"{{{TEXT}}}note-class") in _NOTE_CLASSES
        ):
            continue
        if child.tag == f"{{{TEXT}}}a":
            target = child.attrib.get(f"{{{XLINK}}}href")
            if target:
                targets.append(_bounded_segment_text(target))
        stack.extend(reversed(list(child)))
    return targets


def _link_segments(
    element: ElementTree.Element, prefix: dict[str, object]
) -> list[NormalizedSegment]:
    return [
        NormalizedSegment(locator={**prefix, "hyperlink": number}, text=target)
        for number, target in enumerate(_link_targets(element), 1)
    ]


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


def read_odf_package(
    path: Path, expected_mimetype: str, *, include_objects: bool = False
) -> OdfPackage:
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
            objects: dict[str, bytes] = {}
            if include_objects:
                for name in sorted(names):
                    if name == "content.xml" or not name.endswith("/content.xml"):
                        continue
                    objects[name] = archive.read(name)
    except BadZipFile as error:
        raise ValueError("invalid ODF ZIP package") from error

    _validate_manifest(manifest, expected_mimetype)
    return OdfPackage(
        mimetype=mimetype,
        content=content,
        manifest=manifest,
        objects=objects,
    )


def _read_flat_xml(path: Path) -> bytes:
    if path.stat().st_size > MAX_FLAT_XML_BYTES:
        raise ValueError("flat ODF XML size limit exceeded")
    return path.read_bytes()


def _note_segments(
    note: ElementTree.Element,
    number: int,
    budget: _TableBudget,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    """One note body as its own structure, after the document body.

    The citation marker is not indexed, matching the Word reader, whose paragraph
    text excludes the reference mark. A note without a ``text:id`` is located by its
    1-based position among the notes of that class.
    """

    note_class = note.attrib.get(f"{{{TEXT}}}note-class")
    if note_class not in _NOTE_CLASSES:
        return [], []
    note_id = note.attrib.get(f"{{{TEXT}}}id") or str(number)
    prefix: dict[str, object] = {note_class: note_id}
    body = note.find(f"{{{TEXT}}}note-body")
    if body is None:
        return [], []

    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    paragraph = 0
    table_number = 0
    stack = list(reversed(list(body)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table":
            table_number += 1
            table_segments, table = _read_table(
                element,
                {**prefix, "table": table_number},
                budget,
                label=f"{note_class} table",
                include_links=True,
                drop_notes=True,
            )
            segments.extend(table_segments)
            if table is not None:
                tables.append(table)
            continue
        if element.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            value = _odf_text(element, drop_notes=True)
            if not value and not _link_targets(element):
                continue
            paragraph += 1
            locator: dict[str, object] = {**prefix, "paragraph": paragraph}
            if value:
                segments.append(NormalizedSegment(locator=locator, text=value))
            segments.extend(_link_segments(element, locator))
            continue
        if element.tag == f"{{{TEXT}}}note":
            continue
        stack.extend(reversed(list(element)))
    return segments, tables


def _text_content(
    body: ElementTree.Element,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    """Paragraphs, tables and notes in document order, with no text indexed twice."""

    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    notes = _notes_in(body)
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
                include_links=True,
                drop_notes=True,
            )
            segments.extend(table_segments)
            if table is not None:
                tables.append(table)
            continue
        if element.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            value = _odf_text(element, drop_notes=True)
            if not value and not _link_targets(element):
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
            if value:
                segments.append(NormalizedSegment(locator=locator, text=value))
            segments.extend(_link_segments(element, locator))
            continue
        stack.extend(reversed(list(element)))
    note_numbers: dict[str, int] = {}
    for note in notes:
        note_class = note.attrib.get(f"{{{TEXT}}}note-class") or ""
        note_numbers[note_class] = note_numbers.get(note_class, 0) + 1
        note_segments, note_tables = _note_segments(note, note_numbers[note_class], budget)
        segments.extend(note_segments)
        tables.extend(note_tables)
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


def _bounded_segment_text(value: str) -> str:
    if len(value) > MAX_SEGMENT_CHARACTERS:
        raise ValueError("ODF normalized segment character limit exceeded")
    return value


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
            return _bounded_segment_text(value)
    return None


def _paragraph_texts(
    element: ElementTree.Element,
    *,
    skip_tables: bool = False,
    drop_notes: bool = False,
) -> list[str]:
    """Paragraph text under one container, stopping at each paragraph.

    Stopping at a paragraph means a table cell or frame is read paragraph by
    paragraph. With ``drop_notes``, a footnote or endnote is omitted from the
    paragraph's own text because the text reader indexes it later as its own
    structure. A table subtree is skipped only when the caller asks for the text
    outside a frame's table.
    """

    paragraphs: list[str] = []
    stack = list(reversed(list(element)))
    while stack:
        child = stack.pop()
        if skip_tables and child.tag == f"{{{TABLE}}}table":
            continue
        if child.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            value = _odf_text(child, drop_notes=drop_notes)
            if value:
                paragraphs.append(value)
            continue
        stack.extend(reversed(list(child)))
    return paragraphs


def _cell_text(cell: ElementTree.Element, *, drop_notes: bool = False) -> str | None:
    """A table cell's paragraphs, joined the way the Word reader joins them."""

    joined = _bounded_segment_text("\n".join(_paragraph_texts(cell, drop_notes=drop_notes)))
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
            return _bounded_segment_text(value)
    return None


def _table_rows(table: ElementTree.Element) -> Iterator[ElementTree.Element]:
    """Rows in document order, including rows inside a header or group wrapper."""

    stack = list(reversed(list(table)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table-row":
            yield element
        elif element.tag in _ROW_CONTAINERS:
            stack.extend(reversed(list(element)))


def _has_nested_table_outside_notes(table: ElementTree.Element) -> bool:
    """True when a table sits inside a cell, ignoring note bodies.

    A table inside a footnote or endnote body is read separately as
    that note's own structure, so it must not count as a table nested
    inside the outer cell. A genuinely nested cell table still counts.
    """

    stack = list(table)
    while stack:
        element = stack.pop()
        if (
            element.tag == f"{{{TEXT}}}note"
            and element.attrib.get(f"{{{TEXT}}}note-class") in _NOTE_CLASSES
        ):
            continue
        if element.tag == f"{{{TABLE}}}table":
            return True
        stack.extend(list(element))
    return False


def _read_table(
    table: ElementTree.Element,
    prefix: dict[str, object],
    budget: _TableBudget,
    *,
    label: str,
    include_links: bool = False,
    drop_notes: bool = False,
) -> tuple[list[NormalizedSegment], NormalizedTable | None]:
    """One segment per non-empty cell, located by row and column plus the prefix.

    A covered cell carries no text of its own, matching the Word reader's merge
    handling. A table nested inside a cell is refused rather than silently
    flattened into the outer cell. Link targets are emitted only when the caller asks
    for them, because the PowerPoint table reader does not read links.
    """

    if _has_nested_table_outside_notes(table):
        raise ValueError(f"ODF {label} nests a table inside a cell")
    segments: list[NormalizedSegment] = []
    rows: list[list[object | None]] = []
    for row in _table_rows(table):
        row_repeat = _positive_integer(
            row.attrib.get(f"{{{TABLE}}}number-rows-repeated"),
            label="row repeat",
            maximum=MAX_EXPANDED_ROWS,
        )
        cells: list[tuple[int, str | None, ElementTree.Element]] = []
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
            text = (
                None
                if cell.tag == f"{{{TABLE}}}covered-table-cell"
                else _cell_text(cell, drop_notes=drop_notes)
            )
            cells.append((repeat, text, cell))
        if width == 0:
            continue
        budget.spend(row_repeat, width, label=label)
        for _ in range(row_repeat):
            values: list[object | None] = []
            column = 0
            for repeat, text, cell in cells:
                for _ in range(repeat):
                    column += 1
                    values.append(text)
                    locator: dict[str, object] = {
                        **prefix,
                        "row": len(rows) + 1,
                        "column": column,
                    }
                    if text is not None:
                        segments.append(NormalizedSegment(locator=locator, text=text))
                    if include_links and cell.tag != f"{{{TABLE}}}covered-table-cell":
                        segments.extend(_link_segments(cell, locator))
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
        column_span = _positive_integer(
            cell.attrib.get(f"{{{TABLE}}}number-columns-spanned"),
            label="column span",
            maximum=MAX_COLUMNS_PER_ROW,
        )
        row_span = _positive_integer(
            cell.attrib.get(f"{{{TABLE}}}number-rows-spanned"),
            label="row span",
            maximum=MAX_EXPANDED_ROWS,
        )
        if repeat > 1 and (column_span > 1 or row_span > 1):
            raise ValueError("ODF spreadsheet repeats a merged cell")
        covered = cell.tag == f"{{{TABLE}}}covered-table-cell"
        specs.append(
            _CellSpec(
                repeat=repeat,
                value=None if covered else _cell_value(cell),
                formula=cell.attrib.get(f"{{{TABLE}}}formula"),
                column_span=column_span,
                row_span=row_span,
                links=() if covered else tuple(_link_targets(cell)),
            )
        )
    return specs, width


def _effective_row_width(specs: list[_CellSpec]) -> int:
    """Last column that carries content, ignoring empty trailing padding.

    A repeated empty cell is padding, not work: LibreOffice pads sheets
    with empty repeats out to the full column count, while the real
    content may end far earlier. Middle empty cells still count,
    because they sit before content, and a merged anchor counts
    through the end of its span.
    """

    effective = 0
    column = 0
    for spec in specs:
        for _ in range(spec.repeat):
            column += 1
            if (
                spec.value is not None
                or spec.formula is not None
                or spec.links
                or spec.column_span > 1
                or spec.row_span > 1
            ):
                effective = max(effective, column + spec.column_span - 1)
    return effective


def _hidden_columns(table: ElementTree.Element) -> list[tuple[int, int]]:
    """Expanded column ranges whose ``table:visibility`` hides them."""

    ranges: list[tuple[int, int]] = []
    column = 0
    stack = list(reversed(list(table)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table-column":
            repeat = _positive_integer(
                element.attrib.get(f"{{{TABLE}}}number-columns-repeated"),
                label="column repeat",
                maximum=MAX_COLUMNS_PER_ROW,
            )
            column += repeat
            if column > MAX_COLUMNS_PER_ROW:
                raise ValueError("ODF spreadsheet column limit exceeded")
            if element.attrib.get(f"{{{TABLE}}}visibility") in _HIDDEN_VISIBILITY:
                ranges.append((column - repeat + 1, column))
        elif element.tag in _COLUMN_CONTAINERS:
            stack.extend(reversed(list(element)))
    return ranges


def _column_hidden(ranges: list[tuple[int, int]], column: int) -> bool:
    return any(low <= column <= high for low, high in ranges)


def _hidden_marker(sheet: bool, row: bool, column: bool) -> str | None:
    if sheet:
        return "sheet"
    if row and column:
        return "row and column"
    if row:
        return "row"
    if column:
        return "column"
    return None


def _table_styles_hidden(root: ElementTree.Element) -> dict[str, bool]:
    """Table styles whose ``table:display`` is false, keyed by style name."""

    hidden: dict[str, bool] = {}
    for style in root.iter(f"{{{STYLE}}}style"):
        if style.attrib.get(f"{{{STYLE}}}family") != "table":
            continue
        name = style.attrib.get(f"{{{STYLE}}}name")
        properties = style.find(f"{{{STYLE}}}table-properties")
        if name is None or properties is None:
            continue
        display = properties.attrib.get(f"{{{TABLE}}}display")
        if display is not None and display.lower() in _FALSE_VALUES:
            hidden[name] = True
    return hidden


def _column_letters(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell_reference(row: int, column: int) -> str:
    return f"{_column_letters(column)}{row}"


def _spreadsheet(
    root: ElementTree.Element,
    body: ElementTree.Element,
) -> tuple[list[NormalizedSegment], list[NormalizedTable], list[str]]:
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    hidden_sheets: list[str] = []
    table_styles = _table_styles_hidden(root)
    expanded_cells = 0
    expanded_rows = 0
    for sheet_number, sheet in enumerate(_iter_sheets(body), 1):
        sheet_name = sheet.attrib.get(f"{{{TABLE}}}name") or f"Sheet {sheet_number}"
        sheet_hidden = table_styles.get(
            sheet.attrib.get(f"{{{TABLE}}}style-name") or "",
            False,
        )
        if sheet_hidden:
            hidden_sheets.append(sheet_name)
        hidden_columns = _hidden_columns(sheet)
        hidden_rows: set[int] = set()
        grid: dict[tuple[int, int], object] = {}
        merges: list[tuple[int, int, int, int]] = []
        links: list[tuple[int, int, int, str]] = []
        row_number = 0
        for row in _iter_sheet_rows(sheet):
            row_repeat = _positive_integer(
                row.attrib.get(f"{{{TABLE}}}number-rows-repeated"),
                label="row repeat",
                maximum=MAX_EXPANDED_ROWS,
            )
            specs, _row_width = _row_template(row)
            if row_repeat > 1 and any(spec.column_span > 1 or spec.row_span > 1 for spec in specs):
                raise ValueError("ODF spreadsheet repeats a merged cell")
            if expanded_rows + row_repeat > MAX_EXPANDED_ROWS:
                raise ValueError("ODF expanded row limit exceeded")
            effective_width = _effective_row_width(specs)
            if expanded_cells + effective_width * row_repeat > MAX_EXPANDED_CELLS:
                raise ValueError("ODF expanded cell limit exceeded")
            expanded_rows += row_repeat
            expanded_cells += effective_width * row_repeat
            row_hidden = row.attrib.get(f"{{{TABLE}}}visibility") in _HIDDEN_VISIBILITY

            for _ in range(row_repeat):
                row_number += 1
                if row_hidden:
                    hidden_rows.add(row_number)
                column = 0
                for spec in specs:
                    for _ in range(spec.repeat):
                        column += 1
                        end_row = row_number + spec.row_span - 1
                        end_column = column + spec.column_span - 1
                        if end_column > MAX_COLUMNS_PER_ROW:
                            raise ValueError("ODF spreadsheet column limit exceeded")
                        if spec.value is not None:
                            grid[(row_number, column)] = spec.value
                        if spec.value is not None or spec.formula is not None:
                            locator: dict[str, object] = {
                                "sheet": sheet_name,
                                "row": row_number,
                                "column": column,
                            }
                            if spec.formula is not None:
                                locator["formula"] = spec.formula
                            if spec.column_span > 1 or spec.row_span > 1:
                                locator["merged"] = (
                                    f"{_cell_reference(row_number, column)}:"
                                    f"{_cell_reference(end_row, end_column)}"
                                )
                            hidden = _hidden_marker(
                                sheet_hidden,
                                row_hidden,
                                _column_hidden(hidden_columns, column),
                            )
                            if hidden is not None:
                                locator["hidden"] = hidden
                            segments.append(
                                NormalizedSegment(
                                    locator=locator,
                                    text=_bounded_segment_text(
                                        spec.value if spec.value is not None else spec.formula or ""
                                    ),
                                )
                            )
                        for link_number, target in enumerate(spec.links, 1):
                            links.append((row_number, column, link_number, target))
                        if spec.column_span > 1 or spec.row_span > 1:
                            merges.append((row_number, column, end_row, end_column))

        if grid:
            last_row = max(row for row, _ in grid)
            last_column = max(column for _, column in grid)
            filled = dict(grid)
            for min_row, min_column, max_row, max_column in merges:
                anchor = grid.get((min_row, min_column))
                if anchor is None:
                    continue
                bottom = min(max_row, last_row)
                right = min(max_column, last_column)
                if bottom < min_row or right < min_column:
                    continue
                positions = (bottom - min_row + 1) * (right - min_column + 1)
                if expanded_cells + positions > MAX_EXPANDED_CELLS:
                    raise ValueError("ODF expanded cell limit exceeded")
                expanded_cells += positions
                for row in range(min_row, bottom + 1):
                    for column in range(min_column, right + 1):
                        filled.setdefault((row, column), anchor)
            kept = sorted({row for row, _ in filled})
            if len(kept) * last_column > MAX_EXPANDED_CELLS:
                raise ValueError("ODF expanded cell limit exceeded")
            rows = [
                [filled.get((row, column)) for column in range(1, last_column + 1)] for row in kept
            ]
            tables.append(NormalizedTable(locator={"sheet": sheet_name}, rows=rows))

        for row, column, link_number, target in links:
            locator = {
                "sheet": sheet_name,
                "row": row,
                "column": column,
                "hyperlink": link_number,
            }
            hidden = _hidden_marker(
                sheet_hidden,
                row in hidden_rows,
                _column_hidden(hidden_columns, column),
            )
            if hidden is not None:
                locator["hidden"] = hidden
            segments.append(NormalizedSegment(locator=locator, text=target))
    return segments, tables, hidden_sheets


def _iter_pages(element: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag == f"{{{DRAW}}}page":
            yield child
        else:
            yield from _iter_pages(child)


def _object_text(element: ElementTree.Element) -> str:
    return _bounded_segment_text("\n".join(_paragraph_texts(element)))


_NOTES_FURNITURE = {"page", "page-number", "date-time", "header", "footer"}


def _notes_segments(
    notes: ElementTree.Element,
    slide: int,
    *,
    hidden_slide: bool,
) -> list[NormalizedSegment]:
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
        locator: dict[str, object] = {"slide": slide, "speaker_notes": spoken}
        if hidden_slide:
            locator["hidden"] = "slide"
        segments.append(NormalizedSegment(locator=locator, text=value))
    return segments


def _page_styles_hidden(root: ElementTree.Element) -> dict[str, bool]:
    """Drawing-page styles whose ``presentation:visibility`` is hidden."""

    hidden: dict[str, bool] = {}
    for style in root.iter(f"{{{STYLE}}}style"):
        if style.attrib.get(f"{{{STYLE}}}family") != "drawing-page":
            continue
        name = style.attrib.get(f"{{{STYLE}}}name")
        properties = style.find(f"{{{STYLE}}}drawing-page-properties")
        if name is None or properties is None:
            continue
        visibility = properties.attrib.get(f"{{{PRESENTATION}}}visibility")
        if visibility is not None and visibility.lower() == "hidden":
            hidden[name] = True
    return hidden


def _page_hidden(page: ElementTree.Element, styles: dict[str, bool]) -> bool:
    return styles.get(page.attrib.get(f"{{{DRAW}}}style-name") or "", False)


def _collect_layers(layer_set: ElementTree.Element, layers: dict[str, bool]) -> None:
    for layer in layer_set:
        if layer.tag != f"{{{DRAW}}}layer":
            continue
        name = layer.attrib.get(f"{{{DRAW}}}name")
        if name is None:
            continue
        display = layer.attrib.get(f"{{{DRAW}}}display")
        layers[name] = display is not None and display.lower() == "none"


def _document_layers(body: ElementTree.Element) -> dict[str, bool]:
    layers: dict[str, bool] = {}
    for child in body:
        if child.tag == f"{{{DRAW}}}layer-set":
            _collect_layers(child, layers)
    return layers


def _page_layers(page: ElementTree.Element, document_layers: dict[str, bool]) -> dict[str, bool]:
    layers = dict(document_layers)
    for child in page:
        if child.tag == f"{{{DRAW}}}layer-set":
            _collect_layers(child, layers)
    return layers


def _shape_hidden(element: ElementTree.Element, layers: dict[str, bool]) -> bool:
    layer = element.attrib.get(f"{{{DRAW}}}layer")
    return layer is not None and layers.get(layer, False)


def _placeholder_role(element: ElementTree.Element) -> str | None:
    kind = element.attrib.get(f"{{{PRESENTATION}}}class")
    if not kind:
        return None
    return _PLACEHOLDER_ROLES.get(kind, kind)


def _group_children(group: ElementTree.Element) -> Iterator[ElementTree.Element]:
    for child in group:
        if child.tag.startswith(f"{{{DRAW}}}") and child.tag not in _GROUP_IGNORED:
            yield child


def _object_member(href: str | None) -> str | None:
    """The package member an embedded object reference names, if it is internal."""

    if not href or href.startswith("//") or ":" in href.split("/", 1)[0]:
        return None
    path = posixpath.normpath(href)
    if path.startswith("../") or path.startswith("/") or path == "..":
        return None
    if path.endswith(".xml"):
        return path
    return f"{path}/content.xml"


def _chart_cell(table: ElementTree.Element, row: int, column: int) -> str | None:
    """The value at a 1-based row and column in a chart's embedded data table."""

    current_row = 0
    for row_element in _table_rows(table):
        repeat = _positive_integer(
            row_element.attrib.get(f"{{{TABLE}}}number-rows-repeated"),
            label="chart row repeat",
            maximum=MAX_EXPANDED_ROWS,
        )
        current_row += repeat
        if current_row > MAX_EXPANDED_ROWS:
            raise ValueError("ODF chart row limit exceeded")
        if current_row < row:
            continue
        current_column = 0
        for cell in row_element:
            if cell.tag not in {
                f"{{{TABLE}}}table-cell",
                f"{{{TABLE}}}covered-table-cell",
            }:
                continue
            cell_repeat = _positive_integer(
                cell.attrib.get(f"{{{TABLE}}}number-columns-repeated"),
                label="chart column repeat",
                maximum=MAX_COLUMNS_PER_ROW,
            )
            current_column += cell_repeat
            if current_column > MAX_COLUMNS_PER_ROW:
                raise ValueError("ODF chart column limit exceeded")
            if current_column < column:
                continue
            if cell.tag == f"{{{TABLE}}}covered-table-cell":
                return None
            return _cell_value(cell)
        return None
    return None


def _series_label(chart: ElementTree.Element, address: str | None) -> str | None:
    """A series name from its ``chart:label-cell-address`` in the data table."""

    if not address:
        return None
    match = _CELL_ADDRESS.fullmatch(address)
    if match is None:
        return None
    table_name = match.group("table").strip("'").lstrip("$")
    column = 0
    for character in match.group("column").upper():
        column = column * 26 + ord(character) - 64
    row = int(match.group("row"))
    if not 1 <= row <= MAX_EXPANDED_ROWS or not 1 <= column <= MAX_COLUMNS_PER_ROW:
        return None
    for table in chart.iter(f"{{{TABLE}}}table"):
        if table.attrib.get(f"{{{TABLE}}}name") != table_name:
            continue
        value = _chart_cell(table, row, column)
        if value is not None:
            return value
    return None


def _chart_text_from_part(data: bytes, *, label: str) -> str | None:
    """Title, axis titles and series names from an embedded chart object."""

    root = parse_odf_xml(data, label=label)
    body = root.find(f"{{{OFFICE}}}body")
    if body is None:
        return None
    holder = body.find(f"{{{OFFICE}}}chart")
    if holder is None:
        return None
    chart = holder.find(f"{{{CHART}}}chart")
    if chart is None:
        return None

    lines: list[str] = []
    title = chart.find(f"{{{CHART}}}title")
    if title is not None:
        value = _odf_text(title)
        if value:
            lines.append(value)
    for axis in chart.iter(f"{{{CHART}}}axis"):
        axis_title = axis.find(f"{{{CHART}}}title")
        if axis_title is not None:
            value = _odf_text(axis_title)
            if value:
                lines.append(value)
    for series in chart.iter(f"{{{CHART}}}series"):
        value = _series_label(chart, series.attrib.get(f"{{{CHART}}}label-cell-address"))
        if value:
            lines.append(value)
    return _bounded_segment_text("\n".join(lines)) or None


def _frame_table_segments(
    frame: ElementTree.Element,
    locator: dict[str, object],
    budget: _TableBudget,
) -> tuple[list[NormalizedSegment], list[NormalizedTable]]:
    """A frame's tables and outside-table text, in document order.

    Text that appears before a table is emitted before it, and text
    after it is emitted after it. Consecutive outside-table paragraphs
    stay joined into one segment, as before.
    """

    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            segments.append(NormalizedSegment(locator=dict(locator), text="\n".join(buffer)))
            buffer.clear()

    stack = list(reversed(list(frame)))
    while stack:
        element = stack.pop()
        if element.tag == f"{{{TABLE}}}table":
            flush()
            table_segments, normalized = _read_table(
                element,
                dict(locator),
                budget,
                label="presentation table",
            )
            segments.extend(table_segments)
            if normalized is not None:
                tables.append(normalized)
            continue
        if element.tag in {f"{{{TEXT}}}p", f"{{{TEXT}}}h"}:
            value = _odf_text(element)
            if value:
                buffer.append(value)
            continue
        stack.extend(reversed(list(element)))
    flush()
    return segments, tables


class _PageReader:
    """One page's shapes, groups, tables, charts and hidden markers."""

    def __init__(
        self,
        page_number: int,
        page_key: str,
        *,
        hidden_slide: bool,
        include_notes: bool,
        layers: dict[str, bool],
        objects: dict[str, bytes],
        charts: dict[str, str | None],
        budget: _TableBudget,
        hidden_marker: str,
    ) -> None:
        self.page_number = page_number
        self.page_key = page_key
        self.hidden_slide = hidden_slide
        self.include_notes = include_notes
        self.layers = layers
        self.objects = objects
        self.charts = charts
        self.budget = budget
        self.hidden_marker = hidden_marker
        self.chart_number = 0
        self.segments: list[NormalizedSegment] = []
        self.tables: list[NormalizedTable] = []

    def read(self, page: ElementTree.Element) -> None:
        object_number = 0
        for element in page:
            if element.tag == f"{{{PRESENTATION}}}notes":
                if self.include_notes:
                    self.segments.extend(
                        _notes_segments(
                            element,
                            self.page_number,
                            hidden_slide=self.hidden_slide,
                        )
                    )
                continue
            object_number += 1
            self._shape(
                element,
                {self.page_key: self.page_number, "object": object_number},
                (),
                False,
            )

    def _shape(
        self,
        element: ElementTree.Element,
        base: dict[str, object],
        group_path: tuple[int, ...],
        group_hidden: bool,
    ) -> None:
        locator = dict(base)
        if group_path:
            locator["grouped_shape"] = ".".join(str(part) for part in group_path)
        role = _placeholder_role(element)
        if role is not None:
            locator["placeholder"] = role
        hidden = group_hidden or _shape_hidden(element, self.layers)
        if self.hidden_slide:
            locator["hidden"] = self.hidden_marker
        elif hidden:
            locator["hidden"] = "shape"

        if element.tag == f"{{{DRAW}}}g":
            if len(group_path) >= MAX_GROUP_DEPTH:
                raise ValueError("ODF group nesting limit exceeded")
            for number, child in enumerate(_group_children(element), 1):
                self._shape(child, base, (*group_path, number), hidden)
            return

        table = element.find(f"{{{TABLE}}}table") if element.tag == f"{{{DRAW}}}frame" else None
        if table is not None:
            frame_segments, frame_tables = _frame_table_segments(
                element,
                locator,
                self.budget,
            )
            self.segments.extend(frame_segments)
            self.tables.extend(frame_tables)
            return

        chart_text = self._chart_text(element)
        if chart_text is not None:
            self.chart_number += 1
            self.segments.append(
                NormalizedSegment(
                    locator={**locator, "chart": self.chart_number},
                    text=chart_text,
                )
            )
            remaining = _object_text(element)
            if remaining:
                self.segments.append(NormalizedSegment(locator=locator, text=remaining))
            return

        value = _object_text(element)
        if value:
            self.segments.append(NormalizedSegment(locator=locator, text=value))

    def _chart_text(self, element: ElementTree.Element) -> str | None:
        if element.tag != f"{{{DRAW}}}frame":
            return None
        embedded = element.find(f"{{{DRAW}}}object")
        if embedded is None:
            return None
        member = _object_member(embedded.attrib.get(f"{{{XLINK}}}href"))
        if member is None or member not in self.objects:
            return None
        if member not in self.charts:
            self.charts[member] = _chart_text_from_part(
                self.objects[member],
                label=f"chart object {member}",
            )
        return self.charts[member]


def _page_segments(
    root: ElementTree.Element,
    body: ElementTree.Element,
    *,
    page_key: str,
    include_notes: bool,
    objects: dict[str, bytes],
) -> tuple[list[NormalizedSegment], list[NormalizedTable], int, int]:
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    budget = _TableBudget()
    charts: dict[str, str | None] = {}
    page_styles = _page_styles_hidden(root)
    document_layers = _document_layers(body)
    hidden_pages = 0
    chart_count = 0
    for page_number, page in enumerate(_iter_pages(root), 1):
        hidden = _page_hidden(page, page_styles)
        hidden_pages += int(hidden)
        reader = _PageReader(
            page_number,
            page_key,
            hidden_slide=hidden,
            include_notes=include_notes,
            layers=_page_layers(page, document_layers),
            objects=objects,
            charts=charts,
            budget=budget,
            hidden_marker="slide" if page_key == "slide" else "page",
        )
        reader.read(page)
        segments.extend(reader.segments)
        tables.extend(reader.tables)
        chart_count += reader.chart_number
    return segments, tables, hidden_pages, chart_count


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

    family = ODF_BODY_TAGS.get(kind, "formula")
    package: OdfPackage | None = None
    flat_xml = kind in FLAT_XML_KINDS
    if flat_xml:
        content = _read_flat_xml(path)
        representation = "flat-xml"
    else:
        package = read_odf_package(
            path,
            expected_mimetype,
            include_objects=family in {"presentation", "drawing"},
        )
        content = package.content
        representation = "package"
    root = parse_odf_xml(content, label="content XML")

    tables: list[NormalizedTable] = []
    hidden_sheets: list[str] = []
    hidden_pages = 0
    chart_count = 0
    formula_source: str | None = None
    formula_source_sha256: str | None = None
    if family == "formula":
        segments, formula_source, formula_source_sha256 = _formula_segments(root)
    else:
        body = _validated_body(root, kind, flat_xml=flat_xml)
        if family == "spreadsheet":
            segments, tables, hidden_sheets = _spreadsheet(root, body)
        elif family == "presentation":
            segments, tables, hidden_pages, chart_count = _page_segments(
                root,
                body,
                page_key="slide",
                include_notes=True,
                objects={} if package is None else package.objects,
            )
        elif family == "drawing":
            segments, tables, hidden_pages, chart_count = _page_segments(
                root,
                body,
                page_key="page",
                include_notes=False,
                objects={} if package is None else package.objects,
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
        "processor_version": "5",
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
    if family == "spreadsheet":
        metadata["hidden_sheets"] = hidden_sheets
    if family == "presentation":
        metadata["presentation_notes_extracted"] = any(
            "speaker_notes" in segment.locator for segment in segments
        )
        metadata["hidden_slides"] = hidden_pages
        metadata["charts"] = chart_count
    if family == "drawing":
        metadata["hidden_pages"] = hidden_pages
        metadata["charts"] = chart_count
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
