from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from memory_support import make_archive

from archiv.memory.passages import load_passages
from archiv.search.index import search_index_path
from archiv.storage.layout import ArchivLayout

DOCS = {
    "a.txt": "The Orchard Society met on a quiet day to plan the new harvest festival.",
    "b.txt": "The Harbour Pilots Union met on a windy day to plan the new winter timetable.",
}


def test_a_second_load_comes_from_the_file_next_to_the_index_and_is_identical(
    tmp_path: Path,
) -> None:
    home = make_archive(tmp_path, DOCS)
    first = load_passages(home)
    index = search_index_path(ArchivLayout.resolve(home))
    cache = index.with_name(f"{index.stem}.passages-40.json")
    assert cache.is_file()
    assert load_passages(home) == first


def test_a_changed_index_is_read_again_not_taken_from_the_old_file(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    before = load_passages(home)
    index = search_index_path(ArchivLayout.resolve(home))
    with sqlite3.connect(index) as connection:
        connection.execute(
            "UPDATE segments SET text = text || ' It then rained all afternoon for hours.' "
            "WHERE rowid = 1"
        )
    after = load_passages(home)
    assert after != before
    assert any("rained all afternoon" in p.text for p in after)


def test_a_damaged_cache_file_is_ignored(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    first = load_passages(home)
    index = search_index_path(ArchivLayout.resolve(home))
    cache = index.with_name(f"{index.stem}.passages-40.json")
    cache.write_text("{not json", encoding="utf-8")
    assert load_passages(home) == first


def test_a_home_that_cannot_be_written_still_loads(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    index = search_index_path(ArchivLayout.resolve(home))
    folder = index.parent
    os.chmod(folder, 0o555)
    try:
        assert len(load_passages(home)) == 2
    finally:
        os.chmod(folder, 0o755)
