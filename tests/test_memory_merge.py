from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memory_support import FakeModel, HashEmbedder, make_archive

from archiv.memory.build import build_memory
from archiv.memory.extract import Extraction, Mention
from archiv.memory.merge import canonical_ids, confirm_pairs, merge_prompt, propose_pairs
from archiv.memory.store import open_memory, store_extraction
from archiv.storage.layout import ArchivLayout

SHARED = "The committee met on the old quay to discuss the repair schedule for the winter."
TEXT = {"s1": SHARED, "s2": SHARED, "s3": "Orchards produce pears in the autumn months."}


def _layout(tmp_path: Path) -> ArchivLayout:
    layout = ArchivLayout.resolve(tmp_path / "home")
    layout.ensure()
    return layout


def _seed(conn: sqlite3.Connection) -> None:
    store_extraction(
        conn,
        "a" * 64,
        Extraction(
            (
                Mention("Quay Committee", "a group", "s1"),
                Mention("Harbour Panel", "a group", "s2"),
                Mention("Pear Growers", "a group", "s3"),
            ),
            (),
            0,
        ),
    )


def test_propose_excludes_decided_pairs(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _seed(conn)
        pairs = propose_pairs(conn, HashEmbedder(), TEXT, floor=0.8)
        assert [(a, b) for a, b, _ in pairs] == [(1, 2)]
        conn.execute("INSERT INTO merges VALUES (1, 2, 0, 'no', '[]')")
        assert propose_pairs(conn, HashEmbedder(), TEXT, floor=0.8) == []


def test_confirm_records_verdict_and_reason(tmp_path: Path) -> None:
    def complete(prompt: str) -> str:
        return json.dumps({"same": True, "reason": "the passages describe one group"})

    with open_memory(_layout(tmp_path)) as conn:
        _seed(conn)
        confirmed = confirm_pairs(conn, [(1, 2, 0.9)], complete, "fake", TEXT)
        row = conn.execute("SELECT same, reason, segment_ids_json FROM merges").fetchone()
    assert confirmed == 1
    assert row[0] == 1 and "one group" in row[1]
    assert set(json.loads(row[2])) == {"s1", "s2"}


def test_unparseable_confirm_stores_nothing(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _seed(conn)
        confirmed = confirm_pairs(conn, [(1, 2, 0.9)], lambda p: "I am not sure", "fake", TEXT)
        count = conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0]
        cached = conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0]
    assert confirmed == 0 and count == 0 and cached == 0


def test_canonical_ids_transitive(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _seed(conn)
        conn.execute("INSERT INTO merges VALUES (1, 2, 1, 'x', '[]')")
        conn.execute("INSERT INTO merges VALUES (2, 3, 1, 'x', '[]')")
        assert canonical_ids(conn) == {1: 1, 2: 1, 3: 1}


def test_merge_prompt_cites_both_sides(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _seed(conn)
        prompt, segment_ids = merge_prompt(conn, 1, 3, TEXT)
    assert "Quay Committee" in prompt and "Pear Growers" in prompt
    assert set(segment_ids) == {"s1", "s3"}


def test_build_confirms_merges(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path,
        {
            "a.txt": "The Quay Committee met on the old quay to discuss the repair schedule.",
            "b.txt": "The Harbour Panel met on the old quay to discuss the repair schedule.",
        },
    )
    model = FakeModel(["Quay Committee", "Harbour Panel"])
    base = model.__call__

    def complete(prompt: str) -> str:
        if "same thing" in prompt:
            return json.dumps({"same": True, "reason": "one group, two names"})
        return base(prompt)

    summary = build_memory(
        home=home, complete=complete, model_label="fake", embedder=HashEmbedder(), merge_floor=0.5
    )
    assert summary.merges_confirmed == 1
    with open_memory(ArchivLayout.resolve(home)) as conn:
        assert len(set(canonical_ids(conn).values())) == 1


def test_propose_skips_things_that_only_appear_together(tmp_path: Path) -> None:
    """Two things found only in one shared passage are neighbours, not candidates to merge."""
    with open_memory(_layout(tmp_path)) as conn:
        store_extraction(
            conn,
            "a" * 64,
            Extraction(
                (
                    Mention("Quay Committee", "a group", "s1"),
                    Mention("Harbour Panel", "a group", "s1"),
                ),
                (),
                0,
            ),
        )
        assert propose_pairs(conn, HashEmbedder(), TEXT, floor=0.8) == []
