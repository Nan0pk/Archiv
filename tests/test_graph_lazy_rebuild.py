"""`archiv add` no longer rebuilds the whole entity graph; the graph is rebuilt when asked for.

Measured before this change: the graph rebuild re-read every document on every `add` and
took 56 to 65 percent of each batch (69 s at 1,000 files, 194 s at 6,000), and its memory
use grew with the collection until the machine ran out. The rule from S13 still holds: the
graph is never served out of date, because it is rebuilt before it is read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.graph import freshness
from archiv.graph.builder import rebuild_graph
from archiv.graph.queries import get_entity_profile, query_cross_corpus
from archiv.graph.storage import graph_index_path
from archiv.storage.layout import ArchivLayout

runner = CliRunner()

DOCS = {
    "minutes-1999.txt": "Minutes by Jordan Alvarez with Priya Raman for the archive in 1999.",
    "letter-2004.txt": "A letter from Priya Raman to Jordan Alvarez, written in 2004 on the roof.",
}
LATER = {"memo-2010.txt": "A memo by Marta Okafor and Jordan Alvarez dated 2010 on the budget."}


@pytest.fixture
def rebuild_count(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def counting(home: Path | None = None) -> tuple[int, int]:
        calls.append(1)
        return rebuild_graph(home=home)

    monkeypatch.setattr(freshness, "rebuild_graph", counting)
    return calls


def _write(folder: Path, docs: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (folder / name).write_text(text, encoding="utf-8")


def _add(folder: Path, home: Path) -> None:
    result = runner.invoke(app, ["add", str(folder), "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output


def _stats(home: Path) -> dict[str, object]:
    result = runner.invoke(app, ["graph", "stats", "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _graph_rows(home: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """Everything in the graph except creation times, in a stable order."""
    path = graph_index_path(ArchivLayout.resolve(home))
    connection = sqlite3.connect(path)
    try:
        nodes = connection.execute(
            "SELECT node_id, entity_type, canonical_name, aliases_json, properties_json "
            "FROM nodes ORDER BY node_id"
        ).fetchall()
        edges = connection.execute(
            "SELECT edge_id, source_node_id, target_node_id, relation_type, confidence, status, "
            "citations_json FROM edges ORDER BY edge_id"
        ).fetchall()
    finally:
        connection.close()
    return nodes, edges


def test_add_does_not_rebuild_the_graph(tmp_path: Path, rebuild_count: list[int]) -> None:
    _write(tmp_path / "src", DOCS)
    _add(tmp_path / "src", tmp_path / "home")
    assert rebuild_count == []
    assert not graph_index_path(ArchivLayout.resolve(tmp_path / "home")).is_file()


def test_graph_stats_after_add_is_current_and_built_once(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    first = _stats(home)
    assert first["total_nodes"] > 0  # type: ignore[operator]
    assert rebuild_count == [1]
    assert _stats(home) == first
    assert rebuild_count == [1]  # nothing changed, so the second look does not rebuild


def test_a_later_add_is_picked_up_by_one_more_rebuild(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    before = _stats(home)
    _write(tmp_path / "more", LATER)
    _add(tmp_path / "more", home)
    after = _stats(home)
    assert after["total_nodes"] > before["total_nodes"]  # type: ignore[operator]
    assert rebuild_count == [1, 1]


def test_lazy_graph_equals_an_explicit_rebuild(tmp_path: Path) -> None:
    _write(tmp_path / "src", DOCS)
    _write(tmp_path / "src", LATER)
    lazy, eager = tmp_path / "lazy", tmp_path / "eager"
    _add(tmp_path / "src", lazy)
    _add(tmp_path / "src", eager)
    rebuild_graph(home=eager)
    _stats(lazy)  # first look builds it
    assert _graph_rows(lazy) == _graph_rows(eager)


def test_changed_derived_file_makes_the_graph_stale(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    _stats(home)
    layout = ArchivLayout.resolve(home)
    document = next(layout.derived.glob("*/normalized/document.json"))
    stat = document.stat()
    os.utime(document, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    _stats(home)
    assert rebuild_count == [1, 1]  # `rebuild-derived` rewrites this file under the same hash


def test_graph_built_by_an_older_version_is_rebuilt_once(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    rebuild_graph(home=home)
    connection = sqlite3.connect(graph_index_path(ArchivLayout.resolve(home)))
    connection.execute("DELETE FROM graph_meta")  # what a graph from before this change looks like
    connection.commit()
    connection.close()
    _stats(home)
    _stats(home)
    assert rebuild_count == [1]


def test_one_cross_corpus_query_checks_freshness_once(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    query_cross_corpus(home=home)  # every person in the graph is looked up inside this call
    assert rebuild_count == [1]


def test_entity_profile_is_never_served_from_a_stale_graph(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    get_entity_profile("Jordan Alvarez", home=home)
    assert rebuild_count == [1]
    _write(tmp_path / "more", LATER)
    _add(tmp_path / "more", home)
    get_entity_profile("Jordan Alvarez", home=home)
    assert rebuild_count == [1, 1]


def test_a_confirmed_face_name_makes_the_graph_stale_even_if_the_file_looks_unchanged(
    tmp_path: Path, rebuild_count: list[int]
) -> None:
    """A name changed to one of the same length can leave the face file's size and, when two
    writes land in the same clock tick, its modification time exactly as they were."""
    from archiv.faces.storage import confirm_cluster_name, connect_face_index, face_index_path

    _write(tmp_path / "src", DOCS)
    home = tmp_path / "home"
    _add(tmp_path / "src", home)
    layout = ArchivLayout.resolve(home)
    path = face_index_path(layout)
    with connect_face_index(path) as connection:
        connection.execute(
            "INSERT INTO face_clusters (cluster_id, label, member_count, centroid, created_at,"
            " updated_at) VALUES ('c1', 'Cluster 1', 0, x'00', 'now', 'now')"
        )
        connection.commit()
    confirm_cluster_name(layout, "c1", "Ada Lovelace")
    _stats(home)
    assert rebuild_count == [1]

    stat = path.stat()
    confirm_cluster_name(layout, "c1", "Bob Lovelace")  # same length as the name above
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # as if the clock had not moved on
    assert path.stat().st_size == stat.st_size
    _stats(home)
    assert rebuild_count == [1, 1]
