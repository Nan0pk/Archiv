from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memory_support import FakeModel, HashEmbedder, make_archive

from archiv.memory.build import build_memory
from archiv.memory.extract import Extraction, Mention
from archiv.memory.merge import (
    canonical_ids,
    confirm_pairs,
    digit_signature,
    merge_prompt,
    propose_pairs,
)
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


def _numbers_seed(conn: sqlite3.Connection, forms: list[tuple[str, str]]) -> None:
    store_extraction(
        conn,
        "b" * 64,
        Extraction(tuple(Mention(form, "a number", seg) for form, seg in forms), (), 0),
    )


NUM_TEXT = {
    "n1": SHARED,
    "n2": SHARED,
    "n3": SHARED,
    "n4": "The committee met on the old quay to discuss the repair schedule for the winter.",
    "n5": "Pears grow well in the autumn when the orchard group meets to pick the fruit.",
}


def test_digit_signature_reads_values_not_spelling() -> None:
    assert digit_signature("7,355") == digit_signature("7355") == (7355,)
    assert digit_signature("04 Mar") == (4,)
    assert digit_signature("34.5") != digit_signature("34.50")
    assert digit_signature("٧٣٥٥") == (7355,)  # Arabic-Indic digits read as numbers
    assert digit_signature("no digits here") == ()
    assert digit_signature("9" * 5000)  # absurdly long digit runs must not crash


def test_propose_never_pairs_two_different_numbers(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _numbers_seed(conn, [("7,355", "n1"), ("7,295", "n2")])
        assert propose_pairs(conn, HashEmbedder(), NUM_TEXT, floor=0.5) == []


def test_propose_pairs_the_same_number_written_two_ways(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _numbers_seed(conn, [("7,355", "n1"), ("7355", "n2")])
        pairs = propose_pairs(conn, HashEmbedder(), NUM_TEXT, floor=0.5)
    assert [(a, b) for a, b, _ in pairs] == [(1, 2)]


def test_propose_keeps_a_number_against_a_name(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _numbers_seed(conn, [("Group of Twenty", "n1"), ("G20", "n2")])
        pairs = propose_pairs(conn, HashEmbedder(), NUM_TEXT, floor=0.5)
    assert [(a, b) for a, b, _ in pairs] == [(1, 2)]


def test_propose_reads_non_latin_digits_as_numbers(tmp_path: Path) -> None:
    with open_memory(_layout(tmp_path)) as conn:
        _numbers_seed(conn, [("٧٣٥٥", "n1"), ("7355", "n2")])
        pairs = propose_pairs(conn, HashEmbedder(), NUM_TEXT, floor=0.5)
    assert [(a, b) for a, b, _ in pairs] == [(1, 2)]


def test_propose_reaches_a_twin_buried_among_other_numbers(tmp_path: Path) -> None:
    """Bare numbers look alike, so the same-valued twin must not be crowded out."""
    with open_memory(_layout(tmp_path)) as conn:
        _numbers_seed(
            conn,
            [
                ("7,355", "n1"),
                ("7,295", "n2"),
                ("7,300", "n3"),
                ("7,310", "n3"),
                ("7,320", "n3"),
                ("7355", "n4"),
            ],
        )
        pairs = propose_pairs(conn, HashEmbedder(), NUM_TEXT, k=3, floor=0.5)
    assert (1, 6) in [(a, b) for a, b, _ in pairs]
    assert not any({a, b} <= {1, 2, 3, 4, 5} for a, b, _ in pairs)
