from __future__ import annotations

from pathlib import Path

import pytest

from ingestion_support import VALID_INGESTION_FIXTURES

from archiv.ingestion import ingest_file
from archiv.search import (
    read_source_excerpt,
    rebuild_search_index,
    search_documents,
    validate_citation,
)

MARKERS_AND_LOCATIONS: dict[str, tuple[str, dict[str, object]]] = {
    "ARCHIV-TEXT-MARKER-2026": ("plain-text.txt", {"line": 3}),
    "ARCHIV-PDF-MARKER-2026": ("report.pdf", {"page": 1}),
    "ARCHIV-DOCX-MARKER-2026": ("document.docx", {"paragraph": 2}),
    "ARCHIV-XLSX-MARKER-2026": (
        "workbook.xlsx",
        {"sheet": "Evidence", "cell": "B2"},
    ),
    "ARCHIV-PPTX-MARKER-2026": (
        "presentation.pptx",
        {"slide": 1, "shape": 2},
    ),
}


def _ingest_corpus(corpus: Path, home: Path) -> None:
    for filename in VALID_INGESTION_FIXTURES:
        ingest_file(corpus / filename, home=home)


def test_rebuild_and_search_exact_fixture_locations(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    _ingest_corpus(ingestion_corpus, home)
    build = rebuild_search_index(home=home)

    assert build.object_count == len(VALID_INGESTION_FIXTURES)
    assert build.segment_count >= len(MARKERS_AND_LOCATIONS)
    for marker, (source_name, locator) in MARKERS_AND_LOCATIONS.items():
        results = search_documents(marker, home=home)
        assert len(results) == 1
        result = results[0]
        assert result.citation.source_name == source_name
        assert result.citation.locator == locator
        assert marker in result.text
        assert validate_citation(result.citation, home=home).valid is True
        assert read_source_excerpt(result.citation, home=home) == result.text


def test_metadata_filters_are_exact(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    _ingest_corpus(ingestion_corpus, home)
    rebuild_search_index(home=home)

    results = search_documents(
        "ARCHIV",
        home=home,
        source_name="workbook.xlsx",
        kind="xlsx",
    )
    assert results
    assert {result.citation.source_name for result in results} == {"workbook.xlsx"}
    assert {result.citation.kind for result in results} == {"xlsx"}

    pdf_results = search_documents(
        "ARCHIV-PDF-MARKER-2026",
        home=home,
        media_type="application/pdf",
    )
    assert len(pdf_results) == 1
    assert pdf_results[0].citation.kind == "pdf"


def test_unavailable_media_text_is_not_fabricated(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHIV_OCR", "off")
    home = tmp_path / "archiv-home"
    _ingest_corpus(ingestion_corpus, home)
    rebuild_search_index(home=home)

    assert search_documents("ARCHIV-IMAGE-MARKER-2026", home=home) == []
    assert search_documents("ARCHIV-AUDIO-MARKER-2026", home=home) == []
