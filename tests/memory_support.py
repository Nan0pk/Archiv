"""Shared helpers for the archiv.memory tests: a tiny neutral archive and fakes."""

from __future__ import annotations

from pathlib import Path

from archiv.ingestion import ingest_file
from archiv.search.index import rebuild_search_index


def make_archive(tmp_path: Path, documents: dict[str, str]) -> Path:
    """Ingest each `name -> text` as a .txt file and build the search index."""
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    for name, text in documents.items():
        path = source / name
        path.write_text(text, encoding="utf-8")
        ingest_file(path, home=home)
    rebuild_search_index(home=home)
    return home
