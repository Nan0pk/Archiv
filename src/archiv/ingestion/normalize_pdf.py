# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Layout-aware PDF normalization, coordinates, tables, annotations, and encryption handling."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from archiv.contracts import NormalizedDocument, NormalizedSegment, NormalizedTable
from archiv.ingestion.limits import check_native_pages

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None


def _extract_outlines(
    outline: list[Any], current_page: int = 1, depth: int = 1
) -> list[NormalizedSegment]:
    """Traverse outline/bookmark tree and produce document segments."""
    segments: list[NormalizedSegment] = []
    for item in outline:
        if isinstance(item, list):
            segments.extend(_extract_outlines(item, current_page, depth + 1))
        elif isinstance(item, dict):
            title = item.get("/Title")
            if title:
                page_dest = item.get("/Page")
                page_num = current_page
                if isinstance(page_dest, int):
                    page_num = page_dest + 1
                segments.append(
                    NormalizedSegment(
                        locator={
                            "origin": "bookmark",
                            "page": page_num,
                            "level": depth,
                        },
                        text=str(title).strip(),
                    )
                )
        else:
            title = getattr(item, "title", None)
            if title:
                page_dest = getattr(item, "page", None)
                page_num = current_page
                if isinstance(page_dest, int):
                    page_num = page_dest + 1
                segments.append(
                    NormalizedSegment(
                        locator={
                            "origin": "bookmark",
                            "page": page_num,
                            "level": depth,
                        },
                        text=str(title).strip(),
                    )
                )
    return segments


def _extract_form_fields(reader: PdfReader) -> list[NormalizedSegment]:
    """Extract form field names and values as searchable segments."""
    segments: list[NormalizedSegment] = []
    with contextlib.suppress(Exception):
        fields = reader.get_fields()
        if fields:
            for field_name, field_data in fields.items():
                if not isinstance(field_data, dict):
                    continue
                val = field_data.get("/V")
                if val is not None:
                    text_val = str(val).strip()
                    if text_val:
                        segments.append(
                            NormalizedSegment(
                                locator={
                                    "origin": "form_field",
                                    "field": str(field_name),
                                },
                                text=f"{field_name}: {text_val}",
                            )
                        )
    return segments


def _extract_annotations(page: Any, page_idx: int) -> list[NormalizedSegment]:
    """Extract comments, highlights, and annotations from a PDF page."""
    segments: list[NormalizedSegment] = []
    annots = getattr(page, "annotations", None) or []
    for annot in annots:
        try:
            obj = annot.get_object() if hasattr(annot, "get_object") else annot
            contents = obj.get("/Contents")
            if contents:
                contents_str = str(contents).strip()
                if contents_str:
                    subtype = str(obj.get("/Subtype", "/Text")).lstrip("/").lower()
                    rect = obj.get("/Rect")
                    bbox = [round(float(v), 2) for v in rect] if rect and len(rect) == 4 else None
                    loc: dict[str, object] = {
                        "page": page_idx,
                        "origin": "annotation",
                        "type": subtype,
                    }
                    if bbox:
                        loc["bbox"] = bbox
                    segments.append(
                        NormalizedSegment(
                            locator=loc,
                            text=contents_str,
                        )
                    )
        except Exception:
            continue
    return segments


def _cluster_words_into_lines(
    words: list[dict[str, Any]], vertical_tol: float = 4.0
) -> list[list[dict[str, Any]]]:
    """Cluster words with similar vertical positions into ordered lines."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict[str, Any]]] = []
    current_line: list[dict[str, Any]] = []
    current_top: float | None = None

    for w in sorted_words:
        top = float(w["top"])
        if current_top is None or abs(top - current_top) < vertical_tol:
            current_line.append(w)
            current_top = top if current_top is None else current_top
        else:
            lines.append(sorted(current_line, key=lambda item: float(item["x0"])))
            current_line = [w]
            current_top = top
    if current_line:
        lines.append(sorted(current_line, key=lambda item: float(item["x0"])))
    return lines


def _extract_layout_page_segments(
    page: Any, page_idx: int, table_bboxes: list[tuple[float, float, float, float]]
) -> list[NormalizedSegment]:
    """Extract layout-aware text lines with bounding boxes and column sorting."""
    words = page.extract_words()
    if not words:
        return []

    def in_any_table(w: dict[str, Any]) -> bool:
        x0, top, x1, bottom = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])
        for tb in table_bboxes:
            if x0 >= tb[0] - 2 and top >= tb[1] - 2 and x1 <= tb[2] + 2 and bottom <= tb[3] + 2:
                return True
        return False

    body_words = [w for w in words if not in_any_table(w)]
    if not body_words:
        return []

    # Detect multi-column layout by checking for a central gutter
    page_width = float(getattr(page, "width", 612.0))
    mid = page_width / 2.0
    gutter_margin = 15.0

    has_left = any(float(w["x1"]) < mid - gutter_margin for w in body_words)
    has_right = any(float(w["x0"]) > mid + gutter_margin for w in body_words)
    crosses_gutter = any(
        float(w["x0"]) < mid - gutter_margin and float(w["x1"]) > mid + gutter_margin
        for w in body_words
    )

    if has_left and has_right and not crosses_gutter:
        column_groups = [
            [w for w in body_words if float(w["x1"]) <= mid],
            [w for w in body_words if float(w["x0"]) > mid],
        ]
    else:
        column_groups = [body_words]

    segments: list[NormalizedSegment] = []
    line_counter = 1

    for col_idx, col_words in enumerate(column_groups, 1):
        lines = _cluster_words_into_lines(col_words)
        for line in lines:
            line_text = " ".join(str(w["text"]) for w in line).strip()
            if not line_text:
                continue
            x0 = min(float(w["x0"]) for w in line)
            top = min(float(w["top"]) for w in line)
            x1 = max(float(w["x1"]) for w in line)
            bottom = max(float(w["bottom"]) for w in line)
            loc: dict[str, object] = {
                "page": page_idx,
                "bbox": [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)],
                "line": line_counter,
            }
            if len(column_groups) > 1:
                loc["column"] = col_idx
            segments.append(NormalizedSegment(locator=loc, text=line_text))
            line_counter += 1

    return segments


def normalize_pdf(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Normalize a PDF with layout ordering, bounding boxes, tables, and annotations."""
    # 1. Structural read and encryption check via pypdf
    try:
        reader = PdfReader(path, strict=False)
    except Exception as error:
        raise ValueError(f"malformed PDF file: {error}") from error

    is_encrypted = bool(reader.is_encrypted)
    unlocked_empty = False

    if is_encrypted:
        try:
            decrypt_res = reader.decrypt("")
            if decrypt_res == 0:
                return NormalizedDocument(
                    object_sha256=digest,
                    media_type=media_type,
                    kind="pdf",
                    source_name=source_name,
                    segments=[],
                    metadata={
                        "pages": 0,
                        "pdf_locked": True,
                        "archive_locked": True,
                    },
                )
            unlocked_empty = True
        except (FileNotDecryptedError, Exception):
            return NormalizedDocument(
                object_sha256=digest,
                media_type=media_type,
                kind="pdf",
                source_name=source_name,
                segments=[],
                metadata={
                    "pages": 0,
                    "pdf_locked": True,
                    "archive_locked": True,
                },
            )

    try:
        page_count = len(reader.pages)
    except FileNotDecryptedError:
        return NormalizedDocument(
            object_sha256=digest,
            media_type=media_type,
            kind="pdf",
            source_name=source_name,
            segments=[],
            metadata={
                "pages": 0,
                "pdf_locked": True,
                "archive_locked": True,
            },
        )

    check_native_pages(page_count)

    segments: list[NormalizedSegment] = []
    tables: list[NormalizedTable] = []

    # 2. Extract outline / bookmarks
    outline = getattr(reader, "outline", []) or []
    if outline:
        segments.extend(_extract_outlines(outline))

    # 3. Extract form fields
    segments.extend(_extract_form_fields(reader))

    # 4. Extract attachments metadata
    attachments_dict = getattr(reader, "attachments", None) or {}
    attachment_names = sorted(str(k) for k in attachments_dict)

    # 5. Extract text, layout, coordinates, and tables per page
    plumber_doc = None
    if pdfplumber is not None and not (is_encrypted and not unlocked_empty):
        with contextlib.suppress(Exception):
            plumber_doc = pdfplumber.open(path)

    try:
        for page_idx in range(1, page_count + 1):
            pypdf_page = reader.pages[page_idx - 1]
            segments.extend(_extract_annotations(pypdf_page, page_idx))

            page_tables: list[NormalizedTable] = []
            page_table_bboxes: list[tuple[float, float, float, float]] = []

            if plumber_doc is not None and page_idx - 1 < len(plumber_doc.pages):
                plumber_page = plumber_doc.pages[page_idx - 1]

                # Extract tables with cell coordinates
                raw_tables = plumber_page.extract_tables() or []
                with contextlib.suppress(Exception):
                    for t_obj in plumber_page.find_tables():
                        page_table_bboxes.append(t_obj.bbox)

                for t_idx, raw_tbl in enumerate(raw_tables, 1):
                    cleaned_rows: list[list[object | None]] = []
                    for r_idx, row in enumerate(raw_tbl, 1):
                        row_cells: list[object | None] = []
                        for c_idx, cell in enumerate(row, 1):
                            val = str(cell).strip() if cell is not None else ""
                            row_cells.append(val if val else None)
                            if val:
                                segments.append(
                                    NormalizedSegment(
                                        locator={
                                            "page": page_idx,
                                            "table_index": t_idx,
                                            "row": r_idx,
                                            "column": c_idx,
                                        },
                                        text=val,
                                    )
                                )
                        cleaned_rows.append(row_cells)
                    if cleaned_rows:
                        table_model = NormalizedTable(
                            locator={"page": page_idx, "table_index": t_idx},
                            rows=cleaned_rows,
                        )
                        page_tables.append(table_model)

                tables.extend(page_tables)

                # Extract layout-ordered body segments
                layout_segs = _extract_layout_page_segments(
                    plumber_page, page_idx, page_table_bboxes
                )
                if layout_segs:
                    segments.extend(layout_segs)
                else:
                    # Fallback to plain extract_text if word extraction was empty
                    text = (pypdf_page.extract_text() or "").strip()
                    if text:
                        segments.append(
                            NormalizedSegment(
                                locator={"page": page_idx},
                                text=text,
                            )
                        )
            else:
                # Fallback path when pdfplumber is unavailable
                text = (pypdf_page.extract_text() or "").strip()
                if text:
                    segments.append(
                        NormalizedSegment(
                            locator={"page": page_idx},
                            text=text,
                        )
                    )
    finally:
        if plumber_doc is not None:
            with contextlib.suppress(Exception):
                plumber_doc.close()

    metadata: dict[str, object] = {
        "pages": page_count,
        "tables": len(tables),
    }
    if attachment_names:
        metadata["attachments"] = attachment_names
    if is_encrypted:
        metadata["pdf_encrypted"] = True
    if unlocked_empty:
        metadata["pdf_unlocked_with_empty_password"] = True

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="pdf",
        source_name=source_name,
        segments=segments,
        tables=tables,
        metadata=metadata,
    )
