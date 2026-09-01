"""Legacy binary Microsoft Office normalization.

Only ``.xls`` (Excel 97-2003 / BIFF8) is implemented. ``xlrd`` parses the BIFF
record stream directly and never opens a VBA project storage, so macros are
never executed. ``xlrd`` also refuses to parse an RC4/XOR-obfuscated workbook
(it raises ``xlrd.biffh.XLRDError`` on the ``FILEPASS`` record), which the
generic normalization wrapper in :mod:`archiv.ingestion.normalizers` turns
into a fail-closed ``MalformedInputError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import xlrd
from xlrd.biffh import error_text_from_code
from xlrd.sheet import Cell

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable

XLS_PROCESSOR_VERSION = "1"


def _column_letters(index: int) -> str:
    """Convert a 0-based column index into spreadsheet column letters."""

    letters = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _coordinate(row_index: int, column_index: int) -> str:
    return f"{_column_letters(column_index)}{row_index + 1}"


def _validated_datemode(datemode: int) -> Literal[0, 1]:
    if datemode == 0:
        return 0
    if datemode == 1:
        return 1
    raise ValueError(f"unsupported XLS date mode: {datemode}")


def _numeric(cell: Cell) -> float:
    # xlrd's Cell.value is annotated ``str | float``, but a BOOLEAN cell's value is an
    # actual Python ``int`` (0 or 1) at runtime, so both numeric types are accepted here.
    if isinstance(cell.value, float):
        return cell.value
    if isinstance(cell.value, int):
        return float(cell.value)
    raise ValueError(f"XLS cell type {cell.ctype} must carry a numeric value")


def _cell_value(cell: Cell, *, datemode: Literal[0, 1]) -> object | None:
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return cell.value if cell.value else None
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return _numeric(cell)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(_numeric(cell))
    if cell.ctype == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(_numeric(cell), datemode)
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return error_text_from_code.get(int(_numeric(cell)), "#ERR!")
    raise ValueError(f"unsupported XLS cell type: {cell.ctype}")


def normalize_xls(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Validate and normalize one Excel 97-2003 (BIFF8) workbook without execution."""

    workbook = xlrd.open_workbook(
        str(path),
        formatting_info=False,
        on_demand=False,
        ragged_rows=True,
    )
    datemode = _validated_datemode(workbook.datemode)
    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []
    for sheet in workbook.sheets():
        rows: list[list[object | None]] = []
        for row_index in range(sheet.nrows):
            values: list[object | None] = []
            for column_index in range(sheet.row_len(row_index)):
                value = _cell_value(sheet.cell(row_index, column_index), datemode=datemode)
                values.append(value)
                if value is not None:
                    segments.append(
                        NormalizedSegment(
                            locator={
                                "sheet": sheet.name,
                                "cell": _coordinate(row_index, column_index),
                            },
                            text=str(value),
                        )
                    )
            if any(value is not None for value in values):
                rows.append(values)
        if rows:
            tables.append(NormalizedTable(locator={"sheet": sheet.name}, rows=rows))

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="xls",
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata={
            "processor": "archiv.legacy-office-xls",
            "processor_version": XLS_PROCESSOR_VERSION,
            "sheets": workbook.sheet_names(),
            "biff_version": workbook.biff_version,
            "macros_executed": False,
            "formulas_decompiled": False,
        },
    )


__all__ = ["normalize_xls"]
