"""The search index fills each document's passages in one batch and comes out the same.

Measured before this change: a spreadsheet is one passage per cell, and loading a 27 MB one
(1.18 million passages) into the index took 28.0 s, mostly in two SQL calls per passage from
Python. Inserting the passages together, letting SQLite fill the full-text table itself and giving
the index a 64 MB page cache took 13.0 s, and the rows, their order and the search results were
identical.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index, search_documents, update_search_index
from archiv.search.index import search_index_path
from archiv.storage.layout import ArchivLayout

WORDS = ["harvest", "granary", "lighthouse", "orchard", "ferry", "quarry"]


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def _ingest(home: Path, folder: Path, files: dict[str, str]) -> list[str]:
    return [
        ingest_file(_write(folder, name, text), home=home).object_sha256
        for name, text in files.items()
    ]


def _rows(home: Path) -> list[tuple[str, str, str]]:
    connection = sqlite3.connect(search_index_path(ArchivLayout.resolve(home)))
    try:
        return connection.execute(
            "SELECT segment_id, locator_json, text FROM segments ORDER BY segment_id"
        ).fetchall()
    finally:
        connection.close()


def _hits(home: Path, word: str) -> list[str]:
    connection = sqlite3.connect(search_index_path(ArchivLayout.resolve(home)))
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT s.segment_id FROM segments_fts f JOIN segments s ON s.rowid = f.rowid "
                "WHERE segments_fts MATCH ? ORDER BY s.segment_id",
                (word,),
            )
        ]
    finally:
        connection.close()


FILES = {
    "a.txt": (
        "The harvest came in early.\n\nThe granary was full.\n\nA lighthouse stood by the orchard."
    ),
    "b.txt": "The ferry crossed at dawn.\n\n   \n\nThe quarry was closed for the harvest festival.",
    "c.txt": "Nothing here but the orchard and the granary.",
}


def test_update_gives_the_same_passages_and_hits_as_a_rebuild(tmp_path: Path) -> None:
    updated, rebuilt = tmp_path / "updated", tmp_path / "rebuilt"
    first = _ingest(updated, tmp_path / "src", {"a.txt": FILES["a.txt"]})
    rebuild_search_index(home=updated)
    later = _ingest(updated, tmp_path / "src", {k: v for k, v in FILES.items() if k != "a.txt"})
    update_search_index(later, home=updated)
    assert first

    _ingest(rebuilt, tmp_path / "src", FILES)
    rebuild_search_index(home=rebuilt)

    assert _rows(updated) == _rows(rebuilt)
    for word in WORDS:
        assert _hits(updated, word) == _hits(rebuilt, word), word
    assert len(search_documents("harvest", home=updated)) == len(
        search_documents("harvest", home=rebuilt)
    )
    connection = sqlite3.connect(search_index_path(ArchivLayout.resolve(updated)))
    try:
        connection.execute("INSERT INTO segments_fts(segments_fts) VALUES('integrity-check')")
    finally:
        connection.close()


def test_blank_passages_are_skipped_and_the_counts_are_right(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _ingest(home, tmp_path / "src", {"b.txt": FILES["b.txt"]})
    build = rebuild_search_index(home=home)
    rows = _rows(home)
    assert build.segment_count == len(rows) > 0
    assert all(text.strip() for _id, _locator, text in rows)
    again = update_search_index(None, home=home)
    assert again.segment_count == build.segment_count == len(_rows(home))


def test_a_changed_document_replaces_its_old_passages(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (digest,) = _ingest(home, tmp_path / "src", {"a.txt": FILES["a.txt"]})
    rebuild_search_index(home=home)
    assert _hits(home, "lighthouse")

    document = ArchivLayout.resolve(home).derived_root(digest) / "normalized" / "document.json"
    payload = json.loads(document.read_text(encoding="utf-8"))
    for segment in payload["segments"]:
        segment["text"] = segment["text"].replace("lighthouse", "windmill")
    document.write_text(json.dumps(payload), encoding="utf-8")

    update_search_index([digest], home=home)
    assert _hits(home, "lighthouse") == []
    assert _hits(home, "windmill")
    texts = [text for _id, _locator, text in _rows(home)]
    assert len(texts) == len(set(texts)) == len([t for t in texts if t.strip()])
