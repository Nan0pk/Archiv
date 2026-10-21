"""XLSX normalization: stored results, formulas, links, merges, hidden content, charts.

A workbook cell holding a formula stores two things: the formula text, and the result
the spreadsheet application last calculated and saved beside it. Searching needs the
result, because that is the number a person remembers; a citation needs the formula,
because it says where the number came from. ``openpyxl`` gives one or the other per
load, so the workbook is read twice in read-only mode and walked in step: once for
formula text and once for stored results. Archiv never calculates a formula itself.
When a file has no stored result for a formula, the formula text is indexed instead
and the locator says the result was not saved in the file.

Merged ranges, hyperlinks, hidden rows and columns and charts are not available from a
read-only ``openpyxl`` load. Rather than a second, full in-memory load, the package's
own XML parts are read directly with the standard library, streaming each sheet part
so memory stays flat, within one node budget for the whole workbook. Each part this
reader parses itself is parsed once, however often it is referenced; openpyxl still
reads a sheet's cells once for every time the workbook lists it. Before any of that,
and before openpyxl parses anything, every member of the package is checked for DTD
and entity declarations, which are refused outright.

Hidden sheets, rows and columns are still indexed, so their content can be found, but
every segment from them carries ``hidden``, so an answer drawn from hidden content says
so.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter, range_boundaries
from openpyxl.worksheet.formula import ArrayFormula

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable
from archiv.ingestion.xlsx_process import read_in_worker
from archiv.xml_safety import MalformedXmlError, XmlDeclarationError, refuse_xml_declarations

__all__ = ["MAX_CELL_POSITIONS", "MAX_XML_NODES", "normalize_xlsx"]

# A read-only openpyxl load pads rows and yields missing rows. Explicit bounds cover
# both declared dimensions and actual cells, and are checked before iteration or row
# allocation. A small file must not force enormous padding. The budget counts positions
# across the whole workbook, empty padding included, and refuses the workbook once it
# is spent. It also counts the positions of the tables built from the cells.
MAX_CELL_POSITIONS = 5_000_000
# Every element of every XML part this reader parses itself, counted while streaming
# across the whole workbook, so a part cannot be made expensive by being referenced
# many times. Every cell of a sheet is several elements, so this is set well above the
# cell budget.
MAX_XML_NODES = 25_000_000

_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
# openpyxl chooses the workbook part from [Content_Types].xml in this order. This
# reader chooses it from the package relationships, so the two must agree before the
# sheet budgets and the repeated-sheet check can mean anything.
_WORKBOOK_CONTENT_TYPES = (
    "application/vnd.ms-excel.template.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
)
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_HIDDEN_STATES = {"hidden", "veryHidden"}
_TRUE = {"1", "true"}
_AXES = ("catAx", "valAx", "dateAx", "serAx")
# openpyxl's own marker for a formula whose result it has no value for.
_FORMULA = "f"

type Locator = dict[str, object]


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _relationship_id(element: ElementTree.Element) -> str | None:
    """The ``r:id`` attribute, under either the transitional or the strict namespace."""

    for name, value in element.attrib.items():
        namespace, _, local = name.rpartition("}")
        if local == "id" and "relationships" in namespace:
            return value
    return None


class _Budget:
    def __init__(self) -> None:
        self.used = 0

    def spend(self, positions: int = 1) -> None:
        self.used += positions
        if self.used > MAX_CELL_POSITIONS:
            raise ValueError("XLSX cell position limit exceeded")


class _NotXml(ValueError):
    pass


def _refuse_declarations(data: bytes, *, name: str) -> None:
    """Refuse any DTD or entity declaration, whatever the part's text encoding."""

    try:
        refuse_xml_declarations(data)
    except XmlDeclarationError:
        raise ValueError(f"XLSX XML declarations and entities are not allowed ({name})") from None
    except MalformedXmlError as error:
        raise _NotXml(f"XLSX package part is not well-formed XML ({name})") from error


@dataclass(frozen=True)
class _Relationship:
    type: str
    target: str
    external: bool


class _Package:
    """The parts of an OOXML package, with relationship lookup.

    Every XML part is checked for declarations before anything parses it, openpyxl
    included, and every element this reader parses is counted against one budget for
    the whole workbook. Each part this reader parses itself (sheet structure,
    relationships, drawings and charts) is parsed once, however often it is referenced.
    """

    def __init__(self, archive: ZipFile) -> None:
        self._archive = archive
        self._names = set(archive.namelist())
        self._checked: set[str] = set()
        self._nodes = 0
        self._relationships: dict[str, dict[str, _Relationship]] = {}
        self._charts: dict[str, str] = {}
        self.drawing_charts: dict[str, list[str]] = {}
        self.sheet_structures: dict[str, _Sheet] = {}

    def refuse_declarations_everywhere(self) -> None:
        """Check every member, so openpyxl never parses a declaration either.

        A relationship can point openpyxl at a part with any name, so members are
        not filtered by extension. A member that is not XML at all, such as an image,
        stops the scanner at its first bytes and is left alone; a declaration can only
        come before an XML document's first element, so it cannot hide after that.
        """

        for name in sorted(self._names):
            data = self._archive.read(name)
            if name.lower().endswith((".xml", ".rels")):
                self._check(name, data)
                continue
            try:
                self._check(name, data)
            except _NotXml:
                continue

    def _check(self, name: str, data: bytes) -> None:
        if name not in self._checked:
            _refuse_declarations(data, name=name)
            self._checked.add(name)

    def read(self, part: str) -> bytes | None:
        if part not in self._names:
            return None
        return self._archive.read(part)

    def iterparse(self, part: str, data: bytes) -> Iterator[ElementTree.Element]:
        """Yield each element of a part as it closes, counting it against the budget."""

        self._check(part, data)
        try:
            for _, element in ElementTree.iterparse(BytesIO(data), events=("end",)):
                self._nodes += 1
                if self._nodes > MAX_XML_NODES:
                    raise ValueError(f"XLSX XML node limit exceeded ({part})")
                yield element
        except ElementTree.ParseError as error:
            raise ValueError(f"XLSX package part is not well-formed XML ({part})") from error

    def root(self, part: str, data: bytes) -> ElementTree.Element:
        root: ElementTree.Element | None = None
        for element in self.iterparse(part, data):
            root = element
        if root is None:
            raise ValueError(f"XLSX package part is empty ({part})")
        return root

    def chart_text(self, part: str) -> str | None:
        """A chart part's text, parsed once; ``None`` when the part is missing."""

        if part not in self._charts:
            data = self.read(part)
            if data is None:
                return None
            self._charts[part] = _chart_text(self.root(part, data))
        return self._charts[part]

    def relationships(self, part: str) -> dict[str, _Relationship]:
        if part not in self._relationships:
            self._relationships[part] = self._read_relationships(part)
        return self._relationships[part]

    def _read_relationships(self, part: str) -> dict[str, _Relationship]:
        directory, base = posixpath.split(part)
        rels_part = posixpath.join(directory, "_rels", f"{base}.rels")
        data = self.read(rels_part)
        if data is None:
            return {}
        relationships: dict[str, _Relationship] = {}
        for element in self.root(rels_part, data):
            if element.tag != f"{{{_RELATIONSHIPS}}}Relationship":
                continue
            identifier = element.attrib.get("Id")
            target = element.attrib.get("Target")
            if not identifier or target is None:
                continue
            external = element.attrib.get("TargetMode") == "External"
            if not external:
                target = _resolve(directory, target)
            relationships[identifier] = _Relationship(
                type=element.attrib.get("Type", ""), target=target, external=external
            )
        return relationships


def _resolve(directory: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(directory, target))


def _kind(relationship: _Relationship) -> str:
    return relationship.type.rpartition("/")[2]


@dataclass
class _Sheet:
    name: str
    hidden: bool
    part: str | None
    chartsheet: bool
    max_row: int = 1
    max_column: int = 1
    hidden_rows: set[int] = field(default_factory=lambda: set[int]())
    hidden_columns: list[tuple[int, int]] = field(default_factory=lambda: list[tuple[int, int]]())
    merges: list[tuple[str, int, int, int, int]] = field(
        default_factory=lambda: list[tuple[str, int, int, int, int]]()
    )
    hyperlinks: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    charts: list[str] = field(default_factory=lambda: list[str]())

    def column_hidden(self, column: int) -> bool:
        return any(low <= column <= high for low, high in self.hidden_columns)

    def hidden_marker(self, row: int, column: int) -> str | None:
        if self.hidden:
            return "sheet"
        row_hidden = row in self.hidden_rows
        column_hidden = self.column_hidden(column)
        if row_hidden and column_hidden:
            return "row and column"
        if row_hidden:
            return "row"
        if column_hidden:
            return "column"
        return None


def _positive(value: str | None, *, label: str) -> int:
    if value is None or not value.isdigit() or int(value) < 1:
        raise ValueError(f"XLSX {label} is not a positive whole number: {value!r}")
    return int(value)


def _manifest_workbook_part(package: _Package) -> str | None:
    """The workbook part openpyxl will load, chosen from ``[Content_Types].xml``.

    ``None`` means the manifest names no workbook part; openpyxl then fails on its own,
    so this reader does not need to guess.
    """

    content_types = "[Content_Types].xml"
    data = package.read(content_types)
    if data is None:
        return None
    root = package.root(content_types, data)
    overrides: list[tuple[str, str]] = []
    defaults: set[str] = set()
    for element in root:
        local = _local(element.tag)
        if local == "Override":
            # openpyxl uses ``PartName[1:]`` unconditionally, so reproduce that
            # exactly rather than normalising a missing leading slash away.
            overrides.append(
                (
                    element.attrib.get("PartName", "")[1:],
                    element.attrib.get("ContentType", ""),
                )
            )
        elif local == "Default":
            defaults.add(element.attrib.get("ContentType", ""))
    for content_type in _WORKBOOK_CONTENT_TYPES:
        # openpyxl takes the first matching Override in document order.
        match = next((part for part, declared in overrides if declared == content_type), None)
        if match is not None:
            return match
    if defaults.intersection(_WORKBOOK_CONTENT_TYPES):
        return "xl/workbook.xml"
    return None


def _workbook_sheets(package: _Package) -> list[_Sheet]:
    workbook_part = next(
        (
            relationship.target
            for relationship in package.relationships("").values()
            if _kind(relationship) == "officeDocument" and not relationship.external
        ),
        None,
    )
    if workbook_part is None:
        raise ValueError("XLSX package has no workbook part")
    manifest_part = _manifest_workbook_part(package)
    if manifest_part is not None and manifest_part != workbook_part:
        # openpyxl would read a different part than the one this reader budgets and
        # checks, so the budgets would describe a workbook that is never parsed.
        raise ValueError("XLSX workbook part disagrees with the package manifest")
    data = package.read(workbook_part)
    if data is None:
        raise ValueError("XLSX package workbook part is missing")
    relationships = package.relationships(workbook_part)
    sheets: list[_Sheet] = []
    for element in package.root(workbook_part, data).iter():
        if _local(element.tag) != "sheet":
            continue
        relationship = relationships.get(_relationship_id(element) or "")
        part = None
        chartsheet = False
        if relationship is not None and not relationship.external:
            part = relationship.target
            chartsheet = _kind(relationship) == "chartsheet"
        sheets.append(
            _Sheet(
                name=element.attrib.get("name", ""),
                hidden=element.attrib.get("state") in _HIDDEN_STATES,
                part=part,
                chartsheet=chartsheet,
            )
        )
    return sheets


def _text_of(element: ElementTree.Element) -> str:
    """Text of a chart title or series name: rich text paragraphs, else cached values."""

    paragraphs = [
        "".join(run.text or "" for run in paragraph.iter(f"{{{_A}}}t"))
        for paragraph in element.iter(f"{{{_A}}}p")
    ]
    text = "\n".join(value for value in paragraphs if value)
    if text:
        return text
    return " ".join(value.text for value in element.iter(f"{{{_C}}}v") if value.text)


def _chart_text(root: ElementTree.Element) -> str:
    """A chart's title, axis titles and series names, in that order, one per line."""

    chart = root.find(f"{{{_C}}}chart")
    if chart is None:
        return ""
    lines: list[str] = []
    title = chart.find(f"{{{_C}}}title")
    if title is not None:
        lines.append(_text_of(title))
    for axis in (element for element in chart.iter() if _local(element.tag) in _AXES):
        axis_title = axis.find(f"{{{_C}}}title")
        if axis_title is not None:
            lines.append(_text_of(axis_title))
    for series in chart.iter(f"{{{_C}}}ser"):
        name_element = series.find(f"{{{_C}}}tx")
        if name_element is not None:
            lines.append(_text_of(name_element))
    return "\n".join(line for line in lines if line)


def _drawing_chart_parts(package: _Package, drawing_part: str) -> list[str]:
    """The chart parts a drawing part anchors, in anchor order, each named once."""

    if drawing_part not in package.drawing_charts:
        package.drawing_charts[drawing_part] = _read_drawing_chart_parts(package, drawing_part)
    return package.drawing_charts[drawing_part]


def _read_drawing_chart_parts(package: _Package, drawing_part: str) -> list[str]:
    data = package.read(drawing_part)
    if data is None:
        return []
    relationships = package.relationships(drawing_part)
    charts: dict[str, None] = {}
    for element in package.root(drawing_part, data).iter(f"{{{_C}}}chart"):
        relationship = relationships.get(_relationship_id(element) or "")
        if relationship is None or relationship.external or _kind(relationship) != "chart":
            continue
        charts.setdefault(relationship.target)
    return list(charts)


def _read_sheet_structure(package: _Package, sheet: _Sheet) -> None:
    """Fill in a sheet's merges, links, hidden rows and columns and charts."""

    if sheet.part is None:
        return
    # A workbook may list the same sheet part more than once. Its structure is parsed
    # the first time and shared after that; openpyxl still reads the cells once per
    # listing (see docs/known-issues.md).
    parsed = package.sheet_structures.get(sheet.part)
    if parsed is not None:
        sheet.max_row = parsed.max_row
        sheet.max_column = parsed.max_column
        sheet.hidden_rows = parsed.hidden_rows
        sheet.hidden_columns = parsed.hidden_columns
        sheet.merges = parsed.merges
        sheet.hyperlinks = parsed.hyperlinks
        sheet.charts = parsed.charts
        return
    package.sheet_structures[sheet.part] = sheet
    data = package.read(sheet.part)
    if data is None:
        return
    relationships = package.relationships(sheet.part)
    # A sheet may name the same drawing, and a drawing the same chart, any number of
    # times; each is read once, so repeats cannot multiply the work.
    drawings: dict[str, None] = {}
    row_number = 0
    for element in package.iterparse(sheet.part, data):
        local = _local(element.tag)
        if local == "dimension":
            reference = element.attrib.get("ref", "")
            _, _, max_column, max_row = range_boundaries(reference)
            if max_column is None or max_row is None:
                raise ValueError("XLSX worksheet dimension is not a cell range")
            sheet.max_row = max(sheet.max_row, max_row)
            sheet.max_column = max(sheet.max_column, max_column)
        elif local == "row":
            # A row may omit its number, which then follows the previous row's.
            number = element.attrib.get("r")
            next_row = row_number + 1 if number is None else _positive(number, label="row")
            if next_row <= row_number:
                raise ValueError("XLSX worksheet rows are not in increasing order")
            row_number = next_row
            column_number = 0
            for cell in element:
                if _local(cell.tag) != "c":
                    continue
                coordinate = cell.attrib.get("r")
                if coordinate:
                    cell_row, column = coordinate_to_tuple(coordinate)
                    if cell_row != row_number:
                        raise ValueError("XLSX cell coordinate does not match its row")
                else:
                    column = column_number + 1
                if column <= column_number:
                    raise ValueError("XLSX worksheet cells are not in increasing column order")
                column_number = column
                sheet.max_column = max(sheet.max_column, column)
            sheet.max_row = max(sheet.max_row, row_number)
            if element.attrib.get("hidden") in _TRUE:
                sheet.hidden_rows.add(row_number)
            # Cell values are read by openpyxl; drop them as each row closes.
            element.clear()
        elif local == "col" and element.attrib.get("hidden") in _TRUE:
            low = _positive(element.attrib.get("min"), label="column number")
            high = _positive(element.attrib.get("max"), label="column number")
            sheet.hidden_columns.append((low, high))
        elif local == "mergeCell":
            reference = element.attrib.get("ref", "")
            min_column, min_row, max_column, max_row = range_boundaries(reference)
            if None in (min_column, min_row, max_column, max_row):
                raise ValueError(f"XLSX merged range is not a cell range: {reference!r}")
            sheet.merges.append(
                (reference, min_row or 0, min_column or 0, max_row or 0, max_column or 0)
            )
        elif local == "hyperlink":
            target = ""
            relationship = relationships.get(_relationship_id(element) or "")
            if relationship is not None:
                target = relationship.target
            location = element.attrib.get("location", "")
            if location:
                target = f"{target}#{location}"
            # A link whose relationship is missing has no target to report; skip it.
            if target:
                sheet.hyperlinks.append((element.attrib.get("ref", ""), target))
        elif local == "drawing":
            relationship = relationships.get(_relationship_id(element) or "")
            if relationship is not None and not relationship.external:
                drawings.setdefault(relationship.target)
    chart_parts: dict[str, None] = {}
    for drawing_part in drawings:
        for chart_part in _drawing_chart_parts(package, drawing_part):
            chart_parts.setdefault(chart_part)
    for chart_part in chart_parts:
        text = package.chart_text(chart_part)
        if text is not None:
            sheet.charts.append(text)


def _cell_reference(row: int, column: int) -> str:
    return f"{get_column_letter(column)}{row}"


@dataclass(frozen=True)
class _CellReading:
    text: str
    display: object
    formula: str | None
    missing_result: str | None


def _read_cell(value: object, data_type: str, stored: object) -> _CellReading | None:
    """Pair what a cell holds with the result stored for it, if it holds a formula."""

    if data_type != _FORMULA:
        if value is None:
            return None
        return _CellReading(text=str(value), display=value, formula=None, missing_result=None)
    formula: str | None
    if isinstance(value, ArrayFormula):
        formula = value.text
    elif isinstance(value, str):
        formula = value
    else:
        # A data-table formula stores no formula text of its own; only its result.
        formula = None
    if stored is None or stored == "":
        if formula is None:
            return None
        missing = "not saved in file" if stored is None else "empty text"
        return _CellReading(text=formula, display=formula, formula=formula, missing_result=missing)
    return _CellReading(text=str(stored), display=stored, formula=formula, missing_result=None)


def _cells(
    sheet: _Sheet,
    rows: Iterator[tuple[object, ...]],
    stored_rows: Iterator[tuple[object, ...]],
    budget: _Budget,
) -> tuple[list[NormalizedSegment], dict[tuple[int, int], object]]:
    segments: list[NormalizedSegment] = []
    grid: dict[tuple[int, int], object] = {}
    merge_anchor = {(merge[1], merge[2]): merge[0] for merge in sheet.merges}
    for row, stored_row in zip(rows, stored_rows, strict=True):
        budget.spend(len(row))
        for cell, stored_cell in zip(row, stored_row, strict=True):
            row_number = getattr(cell, "row", None)
            column_number = getattr(cell, "column", None)
            if not isinstance(row_number, int) or not isinstance(column_number, int):
                continue  # openpyxl's padding for positions the file does not store
            reading = _read_cell(
                getattr(cell, "value", None),
                str(getattr(cell, "data_type", "")),
                getattr(stored_cell, "value", None),
            )
            if reading is None:
                continue
            grid[(row_number, column_number)] = reading.display
            locator: Locator = {
                "sheet": sheet.name,
                "cell": _cell_reference(row_number, column_number),
            }
            if reading.formula is not None:
                locator["formula"] = reading.formula
            if reading.missing_result is not None:
                locator["computed_value"] = reading.missing_result
            merged = merge_anchor.get((row_number, column_number))
            if merged is not None:
                locator["merged"] = merged
            hidden = sheet.hidden_marker(row_number, column_number)
            if hidden is not None:
                locator["hidden"] = hidden
            segments.append(NormalizedSegment(locator=locator, text=reading.text))
    return segments, grid


def _table(
    sheet: _Sheet, grid: dict[tuple[int, int], object], budget: _Budget
) -> NormalizedTable | None:
    """The sheet as rows, with each merged range's value repeated across its span.

    Only rows holding a value are kept, and every kept row runs from column A to the
    last column holding a value. A merged range is filled only within that extent, so
    a merge declared across a whole row or column does not create empty cells.
    """

    if not grid:
        return None
    last_row = max(row for row, _ in grid)
    last_column = max(column for _, column in grid)
    filled = dict(grid)
    for _, min_row, min_column, max_row, max_column in sheet.merges:
        anchor = grid.get((min_row, min_column))
        if anchor is None:
            continue
        top, bottom = min_row, min(max_row, last_row)
        left, right = min_column, min(max_column, last_column)
        budget.spend(max(0, bottom - top + 1) * max(0, right - left + 1))
        for row in range(top, bottom + 1):
            for column in range(left, right + 1):
                filled.setdefault((row, column), anchor)
    kept = sorted({row for row, _ in filled})
    budget.spend(len(kept) * last_column)
    rows = [[filled.get((row, column)) for column in range(1, last_column + 1)] for row in kept]
    return NormalizedTable(locator={"sheet": sheet.name}, rows=rows)


def _link_and_chart_segments(sheet: _Sheet, budget: _Budget) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    budget.spend(len(sheet.hyperlinks) + len(sheet.charts))
    for reference, target in sheet.hyperlinks:
        locator: Locator = {"sheet": sheet.name, "cell": reference, "hyperlink": 1}
        hidden: str | None = "sheet" if sheet.hidden else None
        if hidden is None and reference:
            try:
                min_column, min_row, _, _ = range_boundaries(reference)
            except ValueError:
                min_column = min_row = None
            if min_column is not None and min_row is not None:
                hidden = sheet.hidden_marker(min_row, min_column)
        if hidden is not None:
            locator["hidden"] = hidden
        segments.append(NormalizedSegment(locator=locator, text=target))
    for number, text in enumerate(sheet.charts, 1):
        if not text:
            continue
        locator = {"sheet": sheet.name, "chart": number}
        if sheet.hidden:
            locator["hidden"] = "sheet"
        segments.append(NormalizedSegment(locator=locator, text=text))
    return segments


def read_xlsx(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    raw = path.read_bytes()
    # Every member is checked for declarations, and the parts this reader needs are
    # read within one budget, before openpyxl parses anything.
    with ZipFile(BytesIO(raw)) as archive:
        package = _Package(archive)
        package.refuse_declarations_everywhere()
        sheets = _workbook_sheets(package)
        seen_worksheets: set[str] = set()
        for sheet in sheets:
            if sheet.part is not None and not sheet.chartsheet:
                if sheet.part in seen_worksheets:
                    raise ValueError("XLSX repeated worksheet part references are not supported")
                seen_worksheets.add(sheet.part)
            _read_sheet_structure(package, sheet)

    # Do this before openpyxl can allocate a padded row or walk a long empty gap.
    # Each sheet's two views must use identical bounds, including cells outside a
    # stale dimension. The shared budget below still counts tables, links and charts.
    if (
        sum(sheet.max_row * sheet.max_column for sheet in sheets if not sheet.chartsheet)
        > MAX_CELL_POSITIONS
    ):
        raise ValueError("XLSX cell position limit exceeded")

    formulas = load_workbook(BytesIO(raw), read_only=True, data_only=False)
    stored = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    budget = _Budget()
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    worksheet_names = set(formulas.sheetnames)
    for sheet in sheets:
        if not sheet.chartsheet and sheet.name in worksheet_names:
            formula_sheet = formulas[sheet.name]
            stored_sheet = stored[sheet.name]
            sheet_segments, grid = _cells(
                sheet,
                formula_sheet.iter_rows(max_row=sheet.max_row, max_col=sheet.max_column),  # pyright: ignore[reportArgumentType]
                stored_sheet.iter_rows(max_row=sheet.max_row, max_col=sheet.max_column),  # pyright: ignore[reportArgumentType]
                budget,
            )
            segments.extend(sheet_segments)
            table = _table(sheet, grid, budget)
            if table is not None:
                tables.append(table)
        segments.extend(_link_and_chart_segments(sheet, budget))
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="xlsx",
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata={
            "sheets": formulas.sheetnames,
            "hidden_sheets": [sheet.name for sheet in sheets if sheet.hidden],
            "charts": sum(len(sheet.charts) for sheet in sheets),
        },
    )


def normalize_xlsx(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    return read_in_worker(
        path,
        digest,
        source_name=source_name,
        media_type=media_type,
        cell_limit=MAX_CELL_POSITIONS,
        node_limit=MAX_XML_NODES,
    )
