"""Chart text shared by the Excel and PowerPoint readers.

A chart part stores the same DrawingML chart markup whichever application holds it, so
both readers index the same text from it: the title, then axis titles, then series
names, one per line.
"""

from __future__ import annotations

from xml.etree import ElementTree

__all__ = ["CHART_NAMESPACE", "chart_text"]

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
CHART_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_C = CHART_NAMESPACE
_AXES = ("catAx", "valAx", "dateAx", "serAx")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


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


def chart_text(root: ElementTree.Element) -> str:
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
