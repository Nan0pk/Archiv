"""Shared paths and constants for ingestion acceptance tests."""

from pathlib import Path

GENERATOR = Path(__file__).parents[1] / "scripts" / "generate_fixture_corpus.py"
VALID_INGESTION_FIXTURES = (
    "plain-text.txt",
    "report.pdf",
    "document.docx",
    "workbook.xlsx",
    "presentation.pptx",
    "scanned-page.png",
    "sample.wav",
)
