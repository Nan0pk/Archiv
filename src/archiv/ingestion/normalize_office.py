"""Presentation normalization: shapes, groups, tables, charts, speaker notes, hidden marks.

Reading ``shape.text`` for each top-level shape silently drops most of a deck. A table
or a chart sits in a graphic frame, which has no ``text``; a grouped shape's text lives
in the shapes inside the group; and speaker notes are on a separate notes page. This
module walks each of those explicitly and gives every recovered piece of text a
locator that says where in the presentation it came from.

Numbering is unchanged from the reader this replaces: ``shape`` is the 1-based position
among the slide's top-level shapes, counting shapes with no text, and an ordinary
shape's text is exactly what ``python-pptx`` gives for ``shape.text``.

Hidden slides and hidden shapes are still indexed, so their content can be found, but
every segment from them carries ``hidden``, so an answer drawn from hidden content says
so.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, cast
from xml.etree import ElementTree
from xml.parsers import expat
from zipfile import ZipFile

from pptx import Presentation
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.slide import NotesSlide, Slide

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable
from archiv.ingestion.ooxml_charts import CHART_NAMESPACE, chart_text

__all__ = ["MAX_GROUP_DEPTH", "MAX_TABLE_CELLS", "MAX_XML_NODES", "normalize_pptx"]

# Every element this reader walks, across the whole presentation, counted again each
# time the same part is reached through another reference. A chart or notes page can
# be referenced from many places, so counting repeats is what stops a small file from
# multiplying the work.
MAX_XML_NODES = 5_000_000
# Every table position, including the padding that makes each row as wide as the
# widest, so one long row cannot turn many short rows into a huge grid.
MAX_TABLE_CELLS = 200_000
# PowerPoint itself nests groups a handful of levels deep.
MAX_GROUP_DEPTH = 32

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TRUE = {"1", "true"}
_FALSE = {"0", "false"}
_PLACEHOLDER_ROLES = {
    "title": "title",
    "ctrTitle": "title",
    "subTitle": "subtitle",
    "body": "body",
    "obj": "content",
    "dt": "date",
    "ftr": "footer",
    "hdr": "header",
    "sldNum": "slide number",
    "chart": "chart",
    "tbl": "table",
    "pic": "picture",
    "dgm": "diagram",
    "media": "media",
    "clipArt": "clip art",
    "sldImg": "slide image",
}
# Placeholders on a notes page that hold something other than the notes themselves.
_NOTES_PAGE_FURNITURE = {"sldImg", "sldNum", "dt", "hdr", "ftr"}

type Locator = dict[str, object]


class _Xml(Protocol):
    """The part of the lxml element interface this reader uses.

    ``python-pptx`` hands out lxml elements that carry no type information, so this
    names the few calls made on them.
    """

    @property
    def tag(self) -> object: ...

    def __iter__(self) -> Iterator[_Xml]: ...

    def iter(self) -> Iterator[object]: ...

    def find(self, path: str) -> _Xml | None: ...

    def get(self, key: str, default: str = ...) -> str | None: ...


def _xml(element: object) -> _Xml:
    return cast(_Xml, element)


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.cells = 0

    def walk(self, element: _Xml | ElementTree.Element) -> None:
        self.nodes += sum(1 for _ in element.iter())
        if self.nodes > MAX_XML_NODES:
            raise ValueError("PPTX XML node limit exceeded")

    def spend_cells(self, positions: int) -> None:
        self.cells += positions
        if self.cells > MAX_TABLE_CELLS:
            raise ValueError("PPTX table cell limit exceeded")


class _DeclarationRefused(Exception):
    pass


class _NotXml(ValueError):
    pass


def _refuse_declaration(*_: object) -> None:
    raise _DeclarationRefused


def _refuse_declarations(data: bytes, *, name: str) -> None:
    """Refuse any DTD or entity declaration, whatever the part's text encoding."""

    scanner = expat.ParserCreate()
    scanner.StartDoctypeDeclHandler = _refuse_declaration
    scanner.EntityDeclHandler = _refuse_declaration
    try:
        scanner.Parse(data, True)
    except _DeclarationRefused:
        raise ValueError(f"PPTX XML declarations and entities are not allowed ({name})") from None
    except expat.ExpatError as error:
        raise _NotXml(f"PPTX package part is not well-formed XML ({name})") from error


def _refuse_declarations_everywhere(path: Path) -> None:
    """Check every member before python-pptx parses any of them.

    A relationship can point python-pptx at a part with any name, so members are not
    filtered by extension. A member that is not XML at all, such as an image, stops the
    scanner at its first bytes and is left alone; a declaration can only come before an
    XML document's first element, so it cannot hide after that.
    """

    with ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            data = archive.read(name)
            try:
                _refuse_declarations(data, name=name)
            except _NotXml:
                if name.lower().endswith((".xml", ".rels")):
                    raise
                continue


def _non_visual(element: _Xml) -> _Xml | None:
    """The shape's ``nvSpPr``, ``nvGrpSpPr``, ``nvGraphicFramePr`` or similar."""

    for child in element:
        tag = str(child.tag)
        if tag.startswith(f"{{{_P}}}nv"):
            return child
    return None


def _placeholder_type(element: _Xml) -> str | None:
    non_visual = _non_visual(element)
    if non_visual is None:
        return None
    properties = non_visual.find(f"{{{_P}}}nvPr")
    placeholder = None if properties is None else properties.find(f"{{{_P}}}ph")
    if placeholder is None:
        return None
    # A placeholder with no type is a content placeholder.
    return placeholder.get("type", "obj") or "obj"


def _placeholder_role(element: _Xml) -> str | None:
    kind = _placeholder_type(element)
    if kind is None:
        return None
    return _PLACEHOLDER_ROLES.get(kind, kind)


def _shape_hidden(element: _Xml) -> bool:
    non_visual = _non_visual(element)
    if non_visual is None:
        return False
    properties = non_visual.find(f"{{{_P}}}cNvPr")
    return properties is not None and properties.get("hidden", "") in _TRUE


class _SlideReader:
    def __init__(
        self,
        slide_number: int,
        hidden_slide: bool,
        budget: _Budget,
        charts: dict[str, tuple[str, ElementTree.Element]],
    ) -> None:
        self.slide_number = slide_number
        self.hidden_slide = hidden_slide
        self.budget = budget
        self.charts = charts
        self.chart_number = 0
        self.segments: list[NormalizedSegment] = []
        self.tables: list[NormalizedTable] = []

    def shapes(self, shapes: list[BaseShape]) -> None:
        for number, shape in enumerate(shapes, 1):
            self.budget.walk(_xml(shape.element))
            self._shape(shape, {"slide": self.slide_number, "shape": number}, (), False)

    def _shape(
        self, shape: BaseShape, base: Locator, group_path: tuple[int, ...], group_hidden: bool
    ) -> None:
        locator: Locator = dict(base)
        if group_path:
            locator["grouped_shape"] = ".".join(str(part) for part in group_path)
        role = _placeholder_role(_xml(shape.element))
        if role is not None:
            locator["placeholder"] = role
        hidden = group_hidden or _shape_hidden(_xml(shape.element))
        if self.hidden_slide:
            locator["hidden"] = "slide"
        elif hidden:
            locator["hidden"] = "shape"

        if isinstance(shape, GroupShape):
            if len(group_path) >= MAX_GROUP_DEPTH:
                raise ValueError("PPTX group nesting limit exceeded")
            for number, child in enumerate(shape.shapes, 1):
                self._shape(child, base, (*group_path, number), hidden)
            return
        if isinstance(shape, GraphicFrame):
            if shape.has_table:
                self._table(shape, locator)
            elif shape.has_chart:
                self._chart(shape, locator)
            return
        if shape.has_text_frame:
            text: str = getattr(shape, "text", "")
            if text:
                self.segments.append(NormalizedSegment(locator=locator, text=text))

    def _table(self, frame: GraphicFrame, locator: Locator) -> None:
        """One segment per non-empty cell, located by 1-based row and column.

        A cell covered by a merge carries no text of its own and yields nothing.
        """

        rows: list[list[object | None]] = []
        table_rows = frame.table.rows
        for row_index in range(len(table_rows)):
            row_number = row_index + 1
            cells = table_rows[row_index].cells
            values: list[object | None] = []
            for column_index in range(len(cells)):
                column_number = column_index + 1
                cell = cells[column_index]
                self.budget.spend_cells(1)
                text = None if cell.is_spanned else cell.text or None
                if text is not None:
                    self.segments.append(
                        NormalizedSegment(
                            locator={**locator, "row": row_number, "column": column_number},
                            text=text,
                        )
                    )
                values.append(text)
            rows.append(values)
        if not any(value is not None for row in rows for value in row):
            return
        width = max(len(row) for row in rows)
        self.budget.spend_cells(sum(width - len(row) for row in rows))
        padded = [row + [None] * (width - len(row)) for row in rows]
        self.tables.append(NormalizedTable(locator=dict(locator), rows=padded))

    def _chart(self, frame: GraphicFrame, locator: Locator) -> None:
        """The chart's title, axis titles and series names, one per line.

        A chart whose part is missing from the package is skipped, not guessed.
        """

        self.chart_number += 1
        reference = _xml(frame.element).find(f".//{{{CHART_NAMESPACE}}}chart")
        relationship_id = None if reference is None else reference.get(f"{{{_R}}}id")
        if not relationship_id:
            return
        try:
            part = frame.part.related_part(str(relationship_id))
        except KeyError:
            return
        name = str(part.partname)
        if name not in self.charts:
            try:
                root = ElementTree.fromstring(part.blob)
            except ElementTree.ParseError as error:
                raise ValueError(f"PPTX package part is not well-formed XML ({name})") from error
            self.charts[name] = (chart_text(root), root)
        text, root = self.charts[name]
        # Counted on every reference, so one chart shown many times cannot multiply
        # the work unnoticed.
        self.budget.walk(root)
        if text:
            self.segments.append(
                NormalizedSegment(locator={**locator, "chart": self.chart_number}, text=text)
            )

    def notes(self, notes_slide: NotesSlide) -> None:
        """Each text-bearing shape on the notes page, in order, as speaker notes."""

        number = 0
        for shape in notes_slide.shapes:
            # Counted on every slide that shows this notes page.
            self.budget.walk(_xml(shape.element))
            if _placeholder_type(_xml(shape.element)) in _NOTES_PAGE_FURNITURE:
                continue
            if not shape.has_text_frame:
                continue
            text: str = getattr(shape, "text", "")
            if not text:
                continue
            number += 1
            locator: Locator = {"slide": self.slide_number, "speaker_notes": number}
            if self.hidden_slide:
                locator["hidden"] = "slide"
            self.segments.append(NormalizedSegment(locator=locator, text=text))


def _slide_hidden(slide: Slide) -> bool:
    return _xml(slide.element).get("show", "1") in _FALSE


def normalize_pptx(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    _refuse_declarations_everywhere(path)
    presentation = Presentation(str(path))
    budget = _Budget()
    charts: dict[str, tuple[str, ElementTree.Element]] = {}
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    hidden_slides = 0
    slides = list(presentation.slides)
    for slide_number, slide in enumerate(slides, 1):
        hidden = _slide_hidden(slide)
        hidden_slides += hidden
        reader = _SlideReader(slide_number, hidden, budget, charts)
        reader.shapes(list(slide.shapes))
        if slide.has_notes_slide:
            reader.notes(slide.notes_slide)
        segments.extend(reader.segments)
        tables.extend(reader.tables)
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="pptx",
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata={
            "slides": len(slides),
            "hidden_slides": hidden_slides,
            "table_cells": budget.cells,
        },
    )
