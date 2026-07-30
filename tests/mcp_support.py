from __future__ import annotations

from pathlib import Path

from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index

MCP_TEXT_FIXTURES = [
    "plain-text.txt",
    "document.docx",
    "report.pdf",
]


def prepare_mcp_archive(corpus: Path, home: Path) -> None:
    """Build a small deterministic archive for MCP integration tests."""

    for filename in MCP_TEXT_FIXTURES:
        ingest_file(corpus / filename, home=home)
    rebuild_search_index(home=home)
