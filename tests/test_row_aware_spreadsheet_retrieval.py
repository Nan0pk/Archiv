"""Phrases split across adjacent spreadsheet cells must remain retrievable.

``archiv find`` (``search_documents``) indexes each normalized cell
independently and its literal semantics stay unchanged -- this only proves
that ``archiv ask``/``archiv report`` (``retrieve_evidence``) recovers a
phrase whose words land in separate cells of the same row, and that the
citation it returns for that recovery always points at a real cell whose own
text supports it.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook

from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index, retrieve_evidence, search_documents
from archiv.search.service import validate_citation


def _build_quarterly_report_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Report"
    sheet["A1"] = "Quarterly"
    sheet["B1"] = "revenue"
    sheet["C1"] = "growth"
    sheet["A2"] = "Unrelated"
    sheet["B2"] = "maintenance"
    sheet["C2"] = "notes"
    raw = BytesIO()
    workbook.save(raw)
    return raw.getvalue()


def _home_with_report(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    source = tmp_path / "report.xlsx"
    source.write_bytes(_build_quarterly_report_xlsx())
    ingest_file(source, home=home)
    rebuild_search_index(home=home)
    return home


def test_literal_find_still_misses_a_phrase_split_across_cells(tmp_path: Path) -> None:
    home = _home_with_report(tmp_path)
    assert search_documents("quarterly revenue growth", home=home) == []


def test_row_aware_retrieval_recovers_the_split_phrase_honestly(tmp_path: Path) -> None:
    home = _home_with_report(tmp_path)
    package = retrieve_evidence("what is the quarterly revenue growth", home=home)

    row_aware_variants = [
        variant
        for variant in package.diagnostics.query_variants
        if variant.kind == "row-aware-phrase"
    ]
    assert row_aware_variants, package.diagnostics.model_dump(mode="json")
    assert all(variant.result_count > 0 for variant in row_aware_variants)

    matches = [
        result
        for result in package.results
        if result.citation.locator.get("sheet") == "Report"
        and result.citation.locator.get("cell") in {"A1", "B1", "C1"}
    ]
    assert matches, [r.citation.locator for r in package.results]
    for result in matches:
        # Citation honesty: the cited cell's own text is one of the real
        # words in that row, never a fabricated cross-cell concatenation.
        assert result.text.casefold() in {"quarterly", "revenue", "growth"}
        assert validate_citation(result.citation, home=home).valid


def test_row_aware_retrieval_is_stable_across_repeated_calls(tmp_path: Path) -> None:
    home = _home_with_report(tmp_path)
    objective = "what is the quarterly revenue growth"
    first = retrieve_evidence(objective, home=home)
    second = retrieve_evidence(objective, home=home)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
