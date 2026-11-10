"""Passages: de-duplicated text segments read from the existing search index."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from archiv.search.index import search_index_path
from archiv.storage.layout import ArchivLayout

_CACHE_VERSION = 1


@dataclass(frozen=True)
class Passage:
    segment_id: str
    object_sha256: str
    source_name: str
    locator: dict[str, object]
    text: str


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _fingerprint(path: Path, min_chars: int) -> list[object]:
    """What the loaded passages depend on: the search index file as it is now, and `min_chars`."""
    parts: list[object] = [_CACHE_VERSION, min_chars]
    for name in (path.name, f"{path.name}-wal"):
        try:
            stat = (path.parent / name).stat()
            parts += [name, stat.st_size, stat.st_mtime_ns]
        except OSError:
            parts += [name, None, None]
    return parts


def _read_cache(cache: Path, fingerprint: list[object]) -> list[Passage] | None:
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data["fingerprint"] != fingerprint:
            return None
        return [Passage(row[0], row[1], row[2], row[3], row[4]) for row in data["passages"]]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def _write_cache(cache: Path, fingerprint: list[object], passages: list[Passage]) -> None:
    rows = [[p.segment_id, p.object_sha256, p.source_name, p.locator, p.text] for p in passages]
    try:
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"fingerprint": fingerprint, "passages": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(cache)
    except (OSError, ValueError):
        pass  # a read-only home still works, it just reads the index each time


def load_passages(home: Path | None = None, *, min_chars: int = 40) -> list[Passage]:
    """Read every segment once, skipping short ones and exact repeats of earlier text.

    On a large collection this takes seconds, so the result is kept in a file next to the index
    and used again while the index file is unchanged (same size and modification time).
    """
    path = search_index_path(ArchivLayout.resolve(home))
    if not path.is_file():
        raise FileNotFoundError("search index missing; run rebuild-search-index")
    cache = path.with_name(f"{path.stem}.passages-{min_chars}.json")
    fingerprint = _fingerprint(path, min_chars)
    cached = _read_cache(cache, fingerprint)
    if cached is not None:
        return cached
    passages = _read_passages(path, min_chars)
    _write_cache(cache, fingerprint, passages)
    return passages


def _read_passages(path: Path, min_chars: int) -> list[Passage]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT segment_id, object_sha256, source_name, locator_json, text "
            "FROM segments ORDER BY object_sha256, segment_index"
        ).fetchall()
    finally:
        connection.close()
    seen: set[str] = set()
    passages: list[Passage] = []
    for segment_id, object_sha256, source_name, locator_json, text in rows:
        collapsed = _collapse(str(text))
        if len(collapsed) < min_chars:
            continue
        digest = hashlib.sha256(collapsed.casefold().encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        passages.append(
            Passage(
                segment_id=str(segment_id),
                object_sha256=str(object_sha256),
                source_name=str(source_name),
                locator=json.loads(str(locator_json)),
                text=collapsed,
            )
        )
    return passages


def passages_by_object(passages: list[Passage]) -> dict[str, list[Passage]]:
    grouped: dict[str, list[Passage]] = {}
    for passage in passages:
        grouped.setdefault(passage.object_sha256, []).append(passage)
    return grouped


def count_literal_passages(home: Path | None, form: str, *, min_chars: int = 40) -> int:
    """How many distinct passages contain `form` as exact text, ignoring case.

    Counted the same way `load_passages` counts: repeats of the same text count once and
    very short lines are left out, so the number matches what the report step can be given.
    """
    phrase = form.strip()
    if not phrase:
        return 0
    path = search_index_path(ArchivLayout.resolve(home))
    if not path.is_file():
        raise FileNotFoundError("search index missing; run rebuild-search-index")
    match = '"' + phrase.replace('"', '""') + '"'
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT text FROM segments WHERE rowid IN "
            "(SELECT rowid FROM segments_fts WHERE segments_fts MATCH ?)",
            (match,),
        ).fetchall()
    finally:
        connection.close()
    distinct = {_collapse(str(text)).casefold() for (text,) in rows}
    return sum(1 for text in distinct if len(text) >= min_chars)
