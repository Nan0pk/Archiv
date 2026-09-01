"""Minimal local stub for the small xlwt surface Archiv's test fixtures use.

xlwt ships no type information and has no ``types-xlwt`` package on PyPI, and
xlwt itself is test-only (production BIFF8 reading goes through xlrd). This
stub covers exactly the API the fixture builders call.
"""

from datetime import date, datetime
from typing import IO

class XFStyle:
    num_format_str: str

class Formula:
    def __init__(self, formula: str) -> None: ...

class Worksheet:
    def write(
        self,
        row: int,
        column: int,
        label: str | float | bool | date | datetime | Formula | None = ...,
        style: XFStyle | None = ...,
    ) -> None: ...

class Workbook:
    def __init__(self, encoding: str = ...) -> None: ...
    def add_sheet(self, sheetname: str, cell_overwrite_ok: bool = ...) -> Worksheet: ...
    def save(self, filename_or_stream: str | IO[bytes]) -> None: ...
