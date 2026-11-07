from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from memory_support import FakeModel, HashEmbedder, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.memory.ask import (
    NOTHING_FOUND,
    ask,
    check_report,
    entry_prompt,
    parse_entry,
    walk,
)
from archiv.memory.build import build_memory
from archiv.memory.passages import Passage, load_passages
from archiv.memory.store import open_memory
from archiv.storage.layout import ArchivLayout

FORMS = ["Alpha Works", "Beta Trust", "Gamma Society", "Alpha Group"]
DOCS = {
    "a.txt": "Alpha Works signed an agreement with Beta Trust over the northern lease.",
    "b.txt": "Beta Trust later transferred the northern lease to Gamma Society for a fee.",
    "c.txt": "Gamma Society now runs the lease and employs twelve people in the valley.",
    "d.txt": "Alpha Group helped the committee to draft its yearly budget for the valley.",
}


def _built(tmp_path: Path) -> Path:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake", embedder=HashEmbedder())
    return home


def _thing(conn: sqlite3.Connection, form: str) -> int:
    return int(conn.execute("SELECT thing_id FROM things WHERE form = ?", (form,)).fetchone()[0])


def _walk(home: Path, forms: list[str], hops: int = 2) -> list[Passage]:
    layout = ArchivLayout.resolve(home)
    by_id = {p.segment_id: p for p in load_passages(home)}
    with open_memory(layout) as conn:
        return walk(
            conn, forms=forms, question_vec=None, vectors=None, by_id=by_id, home=home, hops=hops
        )


def test_walk_follows_relations_two_hops(tmp_path: Path) -> None:
    home = _built(tmp_path)
    texts = [p.text for p in _walk(home, ["Alpha Works"])]
    assert any("Gamma Society now runs" in t for t in texts)
    assert not any(
        "Gamma Society now runs" in t
        for t in [p.text for p in _walk(home, ["Alpha Works"], hops=1)]
    )


def test_walk_uses_merges(tmp_path: Path) -> None:
    home = _built(tmp_path)
    assert not any("Beta Trust later" in p.text for p in _walk(home, ["Alpha Group"], hops=1))
    with open_memory(ArchivLayout.resolve(home)) as conn:
        a, b = sorted((_thing(conn, "Alpha Works"), _thing(conn, "Alpha Group")))
        conn.execute("INSERT INTO merges VALUES (?, ?, 1, 'same body', '[]')", (a, b))
    assert any("Beta Trust later" in p.text for p in _walk(home, ["Alpha Group"], hops=1))


def test_nothing_found_makes_no_exit_call(tmp_path: Path) -> None:
    home = _built(tmp_path)
    calls: list[str] = []

    def complete(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"forms": ["Zzyzx Qwerty"], "related": []})

    report = ask(
        "quantum pancake zebra",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=HashEmbedder(),
    )
    assert report.text == NOTHING_FOUND
    assert report.cited == []
    assert len(calls) == 1


def test_check_report_flags_unknown_and_uncited() -> None:
    passages = [Passage(f"{i:064x}", "a" * 64, "doc.txt", {"line": i}, f"text {i}") for i in (1, 2)]
    answer = (
        "The first thing happened early on [P1]. Another thing is claimed with no source at all."
    )
    report = check_report(answer + " It was seen again [P7].", passages)
    assert report.unknown_labels == ["P7"]
    assert report.uncited_sentences == 1
    assert [p.segment_id for p in report.cited] == [passages[0].segment_id]
    assert report.labels == [1]


def test_entry_prompt_and_parse() -> None:
    assert "the question" in entry_prompt("the question")
    answer = 'ok {"forms": ["A b", " ", "A b"], "related": ["C"]}'
    assert parse_entry(answer) == ["A b", "C"]


def test_cli_ask_prints_sources(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    script = (
        "import sys, json; p = sys.stdin.read()\n"
        "print(json.dumps({'forms': ['Alpha Works'], 'related': []}) if '\"forms\"' in p "
        "else 'Alpha Works signed an agreement with Beta Trust [P1].')"
    )
    script_path = tmp_path / "model.py"
    script_path.write_text(script, encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "memory",
            "ask",
            "What do we know about Alpha Works?",
            "--home",
            str(home),
            "--model-cmd",
            f"{sys.executable} {script_path}",
            "--model-label",
            "echo",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "[P1]" in result.output
    assert "a.txt" in result.output
