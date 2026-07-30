from __future__ import annotations

from pathlib import Path

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index

REPORT_FIXTURES = [
    "plain-text.txt",
    "report.pdf",
    "document.docx",
    "workbook.xlsx",
    "presentation.pptx",
]


def prepare_report_archive(corpus: Path, home: Path) -> dict[Path, str]:
    """Ingest the text-bearing corpus and return canonical hashes before reporting."""

    originals: dict[Path, str] = {}
    for filename in REPORT_FIXTURES:
        result = ingest_file(corpus / filename, home=home)
        original = Path(result.original_path)
        originals[original] = sha256_file(original)
    rebuild_search_index(home=home)
    return originals
