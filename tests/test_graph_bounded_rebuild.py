"""The graph is built in a working file, a document at a time, and comes out the same.

Before this change `rebuild_graph` kept every node and every edge, with all its citations, in
memory until the end. On a collection of about 6,600 documents (54,000 nodes, 2.2 million edges)
that needed more than 8 GB. These tests pin down that the graph built a piece at a time is the
graph that was built all at once, including the parts that are easy to get wrong when the work
is split: an edge met again in a later piece, a confirmed name taking over the evidence of a
mention, and a document that fails half way.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from archiv.faces.contracts import FaceDetection
from archiv.faces.storage import (
    confirm_cluster_name,
    connect_face_index,
    face_index_path,
    save_detection,
)
from archiv.graph import builder
from archiv.graph.builder import rebuild_graph
from archiv.graph.storage import graph_index_path
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

Rows = list[tuple[object, ...]]


def dump_graph(home: Path) -> tuple[Rows, Rows]:
    """Everything stored in the graph except creation times, in the order it was stored."""
    path = graph_index_path(ArchivLayout.resolve(home))
    connection = sqlite3.connect(path)
    try:
        nodes = connection.execute(
            "SELECT node_id, entity_type, canonical_name, aliases_json, properties_json "
            "FROM nodes ORDER BY rowid"
        ).fetchall()
        edges = connection.execute(
            "SELECT edge_id, source_node_id, target_node_id, relation_type, confidence, status, "
            "citations_json FROM edges ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    return nodes, edges


def _detection(object_sha256: str, source_name: str, face_id: str) -> FaceDetection:
    return FaceDetection(
        face_id=face_id,
        object_sha256=object_sha256,
        source_name=source_name,
        bbox=[0.1, 0.1, 0.9, 0.9],
        confidence=0.9,
        embedding=[0.0] * 64,
    )


def _derived_document(home: Path, source_name: str) -> Path:
    layout = ArchivLayout.resolve(home)
    connection = sqlite3.connect(layout.database)
    try:
        row = connection.execute(
            "SELECT object_sha256 FROM ingestions WHERE source_name = ? LIMIT 1", (source_name,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None, source_name
    return layout.derived_root(str(row[0])) / "normalized" / "document.json"


def _write_segments(path: Path, segments: list[object]) -> None:
    path.write_text(json.dumps({"segments": segments}), encoding="utf-8")


def make_home(tmp_path: Path) -> Path:
    """A collection that exercises every way an edge or a node can be met twice."""
    home = tmp_path / "home"
    src = tmp_path / "src"
    (src / "other").mkdir(parents=True)
    # Same pair three times in one passage, in two documents, in two different years.
    (src / "minutes-1999.txt").write_text(
        "Ada Lovelace met Charles Babbage in 1999. Ada Lovelace and Charles Babbage met again.",
        encoding="utf-8",
    )
    (src / "notes.txt").write_text(
        "Grace Hopper wrote to Ada Lovelace in 2004.\n\nGrace Hopper wrote once more.",
        encoding="utf-8",
    )
    # A second, different document with the same file name: one node for the name, first wins.
    (src / "other" / "notes.txt").write_text(
        "Charles Babbage sent Grace Hopper the plans in 1999.", encoding="utf-8"
    )
    (src / "broken.txt").write_text("Alan Turing and Grace Hopper in 1950.", encoding="utf-8")
    (src / "halfway.txt").write_text("placeholder", encoding="utf-8")
    Image.new("RGB", (32, 32), (200, 120, 90)).save(src / "photo_1998.png")
    Image.new("RGB", (32, 32), (20, 90, 160)).save(src / "snapshot.png")
    for path in sorted(src.rglob("*")):
        if path.is_file():
            ingest_file(path, home=home)

    # One document cannot be read at all; one fails after its first passage.
    _derived_document(home, "broken.txt").write_text("{ not json", encoding="utf-8")
    _write_segments(
        _derived_document(home, "halfway.txt"),
        [
            {
                "text": "Ada Lovelace worked with Grace Hopper in 1999.",
                "locator": {"segment_index": 0},
            },
            "this entry is not a passage",
            {"text": "Never Reached works with Nobody Here.", "locator": {"segment_index": 2}},
        ],
    )

    layout = ArchivLayout.resolve(home)
    connection = sqlite3.connect(layout.database)
    try:
        shas = {
            str(name): str(sha)
            for sha, name in connection.execute("SELECT object_sha256, source_name FROM ingestions")
        }
    finally:
        connection.close()
    with connect_face_index(face_index_path(layout)) as faces:
        for cluster_id, label in (("c1", "Cluster 1"), ("c2", "Cluster 2")):
            faces.execute(
                "INSERT INTO face_clusters (cluster_id, label, member_count, centroid, "
                "created_at, updated_at) VALUES (?, ?, 1, x'00', 'now', 'now')",
                (cluster_id, label),
            )
        save_detection(
            faces, _detection(shas["photo_1998.png"], "photo_1998.png", "f1"), cluster_id="c1"
        )
        save_detection(
            faces, _detection(shas["snapshot.png"], "snapshot.png", "f2"), cluster_id="c2"
        )
        faces.commit()
    # c1 is confirmed as a name that the text mentions as well; c2 is never confirmed.
    confirm_cluster_name(layout, "c1", "Ada Lovelace")
    return home


def test_graph_holds_the_edge_cases(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    rebuild_graph(home=home)
    nodes, edges = dump_graph(home)
    node_ids = {str(n[0]): n for n in nodes}
    by_name = {(str(n[1]), str(n[2])): str(n[0]) for n in nodes}

    # The confirmed name took over the text mention; no separate mention node is left.
    person = by_name[("person", "Ada Lovelace")]
    assert ("candidate_mention", "Ada Lovelace") not in by_name
    assert ("candidate_mention", "Charles Babbage") in by_name
    mentions_of_ada = [e for e in edges if e[1] == person and e[3] == "mentioned_in"]
    assert mentions_of_ada, "the evidence from the text should now belong to the person"
    assert all(str(e[1]) in node_ids and str(e[2]) in node_ids for e in edges)

    # The unconfirmed cluster is still a person, at the lowest status.
    cluster_two = by_name[("person", "Cluster 2")]
    assert {e[5] for e in edges if e[1] == cluster_two} == {"possible"}

    # The photograph named for a year gave its person that year.
    year_1998 = by_name[("date", "1998")]
    assert any(e[1] == person and e[2] == year_1998 and e[3] == "associated_with" for e in edges)

    # Two different files with one name are one node.
    assert sum(1 for n in nodes if n[1] == "document" and n[2] == "notes.txt") == 1

    # The pair seen twice in one passage is cited once for that place, in each direction.
    babbage = by_name[("candidate_mention", "Charles Babbage")]
    pair = next(e for e in edges if e[1] == person and e[2] == babbage and e[3] == "co_occurs_with")
    locators = [
        (c["object_sha256"], json.dumps(c["locator"], sort_keys=True))
        for c in json.loads(str(pair[6]))
    ]
    assert len(locators) == len(set(locators))

    # The document that failed half way keeps its first passage and nothing after it.
    assert ("candidate_mention", "Never Reached") not in by_name
    hopper = by_name[("candidate_mention", "Grace Hopper")]
    assert any(e[1] == person and e[2] == hopper and e[3] == "co_occurs_with" for e in edges)


@pytest.mark.parametrize("flush_at", [1, 2, 7])
def test_writing_out_more_often_gives_the_same_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flush_at: int
) -> None:
    home = make_home(tmp_path)
    rebuild_graph(home=home)
    whole = dump_graph(home)
    monkeypatch.setattr(builder, "_FLUSH_EDGES", flush_at)
    flushes: list[int] = []
    real_flush = builder._Work.flush  # pyright: ignore[reportPrivateUsage]

    def counting(self: builder._Work) -> None:  # pyright: ignore[reportPrivateUsage]
        flushes.append(1)
        real_flush(self)

    monkeypatch.setattr(builder._Work, "flush", counting)  # pyright: ignore[reportPrivateUsage]
    rebuild_graph(home=home)
    assert dump_graph(home) == whole
    assert len(flushes) > 10, "the small limit should have forced writes inside documents"


def test_a_failed_write_stops_the_rebuild_and_leaves_no_working_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = make_home(tmp_path)
    rebuild_graph(home=home)
    layout = ArchivLayout.resolve(home)
    before = graph_index_path(layout).read_bytes()

    def full_disk(_conn: sqlite3.Connection, _edge: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(builder, "upsert_edge", full_disk)
    with pytest.raises(builder._WorkFileError):  # pyright: ignore[reportPrivateUsage]
        rebuild_graph(home=home)
    assert graph_index_path(layout).read_bytes() == before
    assert list(layout.temporary.iterdir()) == []
