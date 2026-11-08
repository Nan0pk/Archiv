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
    exit_prompt,
    parse_entry,
    walk,
)
from archiv.memory.build import build_memory
from archiv.memory.passages import Passage, load_passages
from archiv.memory.store import open_memory
from archiv.memory.vectors import VectorIndex
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


def _walk(home: Path, forms: list[str], hops: int = 2, **options: int) -> list[Passage]:
    layout = ArchivLayout.resolve(home)
    by_id = {p.segment_id: p for p in load_passages(home)}
    with open_memory(layout) as conn:
        return walk(
            conn,
            forms=forms,
            question_vec=None,
            vectors=None,
            by_id=by_id,
            home=home,
            hops=hops,
            **options,
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
    assert report.text.startswith(NOTHING_FOUND)
    assert report.cited == []
    assert len(calls) == 1


def test_check_report_flags_unknown_and_uncited() -> None:
    passages = [Passage(f"{i:064x}", "a" * 64, "doc.txt", {"line": i}, f"text {i}") for i in (1, 2)]
    answer = (
        "The first thing happened early on [P1]. A second claim sits next to it with no label. "
        "Another thing is claimed with no source at all anywhere. "
        "A fourth claim follows it also with no label at all. It was seen again [P7]."
    )
    report = check_report(answer, passages)
    assert report.unknown_labels == ["P7"]
    assert report.uncited_sentences == 1  # only the one with no label on either side
    assert [p.segment_id for p in report.cited] == [passages[0].segment_id]
    assert report.labels == [1]


def test_check_report_accepts_a_label_on_the_neighbouring_sentence() -> None:
    passages = [Passage("1" * 64, "a" * 64, "doc.txt", {"line": 1}, "text one")]
    before = (
        "Kremlin spokesman said the country would help with the nuclear talks. "
        "Trump raised it in February [P1]."
    )
    after = (
        "The envoy asked for an honest inquiry into the departure [P1]. "
        "He said it led to the change."
    )
    assert check_report(before, passages).uncited_sentences == 0
    assert check_report(after, passages).uncited_sentences == 0


def test_check_report_does_not_flag_the_models_own_count_statements() -> None:
    passages = [Passage("1" * 64, "a" * 64, "doc.txt", {"line": 1}, "text one")]
    answer = (
        "The collection has 4 passages that mention 'the Minister', and I was given all 4.\n\n"
        "Sentences like this one claim something with no label anywhere near it at all."
    )
    assert check_report(answer, passages).uncited_sentences == 1  # only the real claim


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


def _guild_archive(tmp_path: Path, tagged: int = 6, untagged: int = 3) -> Path:
    """A guild found in `tagged` passages the model tagged, plus `untagged` passages that
    mention it only in lower case, which the extraction missed but the text index finds."""
    names = [f"Neighbour{i}" for i in range(tagged)]
    docs: dict[str, str] = {}
    for i, name in enumerate(names):
        docs[f"t{i}.txt"] = (
            f"The Pier Guild and the {name} agreed to share the yard beside the river {i}."
        )
        docs[f"n{i}.txt"] = (
            f"The {name} rebuilt the yard fence at the edge of the river bank number {i}."
        )
    for i in range(untagged):
        docs[f"u{i}.txt"] = (
            f"the pier guild pier guild pier guild held a long meeting about the harbour {i}."
        )
    home = make_archive(tmp_path, docs)
    build_memory(home=home, complete=FakeModel(["Pier Guild", *names]), model_label="fake")
    return home


def test_walk_keeps_exact_text_matches_when_the_graph_is_large(tmp_path: Path) -> None:
    home = _guild_archive(tmp_path)
    got = _walk(home, ["Pier Guild"], hops=1, max_passages=8, text_limit=20)
    assert len(got) <= 8
    assert sum(1 for p in got if p.source_name.startswith("u")) == 3


def test_walk_survives_more_connected_things_than_sqlite_allows(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path, {"a.txt": "The Pier Guild keeps its records in the old harbour office."}
    )
    passage = load_passages(home)[0]
    layout = ArchivLayout.resolve(home)
    with open_memory(layout) as conn:
        conn.execute("INSERT INTO things (ident, form) VALUES ('pier guild', 'Pier Guild')")
        conn.executemany(
            "INSERT INTO things (ident, form) VALUES (?, ?)",
            [(f"neighbour {i}", f"Neighbour {i}") for i in range(40000)],
        )
        conn.execute(
            "INSERT INTO forms SELECT thing_id, form, '', ?, ? FROM things",
            (passage.segment_id, passage.object_sha256),
        )
        conn.execute(
            "INSERT INTO relations "
            "SELECT 1, 'works with', thing_id, ?, ? FROM things WHERE thing_id > 1",
            (passage.segment_id, passage.object_sha256),
        )
    assert [p.segment_id for p in _walk(home, ["Pier Guild"], hops=2)] == [passage.segment_id]


def test_walk_starts_from_names_that_contain_the_asked_for_form(tmp_path: Path) -> None:
    """Cheap extraction draws name boundaries differently, so exact equality is not enough."""
    home = make_archive(
        tmp_path,
        {
            "a.txt": "The Ministry of Rural Rehab announced work on four water networks this week.",
            "b.txt": "Work on the water networks began in three districts of the valley.",
        },
    )
    model = FakeModel(["Ministry of Rural Rehab", "water networks"])
    build_memory(home=home, complete=model, model_label="fake")
    texts = [p.text for p in _walk(home, ["Rural Rehab"], hops=1)]
    assert any("began in three districts" in t for t in texts)


def test_walk_does_not_expand_through_names_that_are_everywhere(tmp_path: Path) -> None:
    docs = {
        f"h{i}.txt": f"The Big Country was mentioned in report number {i} about trade."
        for i in range(15)
    }
    docs["s.txt"] = "The Pier Guild and the Big Country signed a short treaty about the harbour."
    docs["x.txt"] = "The Big Country and the Far Island started a long dispute over the sea lanes."
    home = make_archive(tmp_path, docs)
    build_memory(
        home=home,
        complete=FakeModel(["Pier Guild", "Big Country", "Far Island"]),
        model_label="fake",
    )
    texts = [p.text for p in _walk(home, ["Pier Guild"], hops=2)]
    assert any("short treaty" in t for t in texts)
    assert not any("long dispute" in t for t in texts)


def test_walk_ranks_by_similarity_and_leaves_room_beyond_exact_matches(tmp_path: Path) -> None:
    home = _guild_archive(tmp_path)
    layout = ArchivLayout.resolve(home)
    by_id = {p.segment_id: p for p in load_passages(home)}
    embedder = HashEmbedder()
    build_memory(home=home, complete=FakeModel([]), model_label="fake", embedder=embedder)
    index = VectorIndex.load(layout.indexes / "memory-passages")
    question = "river bank yard fence"
    with open_memory(layout) as conn:
        got = walk(
            conn,
            forms=["Pier Guild"],
            question_vec=embedder.embed([question])[0],
            vectors=index,
            by_id=by_id,
            home=home,
            hops=1,
            max_passages=6,
            text_limit=20,
        )
    assert len(got) == 6
    # at most half the slots go to exact matches, so the passages closest in meaning get room
    assert sum(1 for p in got if p.source_name.startswith("n")) >= 2


def test_entry_prompt_shows_the_collections_own_wording() -> None:
    prompt = entry_prompt("What is the TTP?", ["TTP claimed an attack on a checkpoint."])
    assert "TTP claimed an attack on a checkpoint." in prompt
    assert "this collection" in prompt.lower()
    assert "TTP claimed" not in entry_prompt("What is the TTP?")


def test_exit_prompt_shows_document_names_and_true_counts() -> None:
    passage = Passage(
        "1" * 64,
        "a" * 64,
        "Daily report (8 May 2024).docx",
        {"paragraph": 3},
        "Something happened here today in the valley.",
    )
    prompt = exit_prompt("What about the guild?", [passage], [("Pier Guild", 7, 1)])
    assert "[P1] (Daily report (8 May 2024).docx) Something happened" in prompt
    assert "has 7 passages that mention 'Pier Guild'" in prompt
    assert "given 1 of them" in prompt
    assert "Gaps:" in prompt
    assert "has 1 passage that" in exit_prompt("q", [passage], [("X", 1, 1)])


def test_check_report_ignores_the_gaps_paragraph_and_headings() -> None:
    passages = [Passage("1" * 64, "a" * 64, "doc.txt", {"line": 1}, "text one")]
    answer = (
        "**Overview**\n\n**Claims made by several groups about the attacks**\n\n"
        "The first thing happened early on [P1].\n\n"
        "Gaps: The passages do not say when it ended or who else was involved."
    )
    assert check_report(answer, passages).uncited_sentences == 0
    claim = (
        "The first thing happened early on [P1].\n\nAnother thing is claimed with no source at all."
    )
    assert check_report(claim, passages).uncited_sentences == 1


def test_ask_gives_the_entry_step_nearby_passages_and_the_report_step_names_and_counts(
    tmp_path: Path,
) -> None:
    home = _built(tmp_path)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if '"forms"' in prompt:
            return json.dumps({"forms": ["Alpha Works"], "related": []})
        return "Alpha Works signed an agreement with Beta Trust [P1]."

    ask(
        "Who signed the northern lease?",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=HashEmbedder(),
    )
    entry = next(p for p in prompts if '"forms"' in p)
    report = next(p for p in prompts if '"forms"' not in p)
    assert "Alpha Works signed an agreement" in entry  # a nearby passage, shown to the entry step
    assert "(a.txt)" in report

    assert "has 1 passage that mentions 'Alpha Works'" in report


def test_nothing_found_says_what_was_searched_and_does_not_claim_absence(tmp_path: Path) -> None:
    home = _built(tmp_path)

    def complete(prompt: str) -> str:
        return json.dumps({"forms": ["Zzyzx Qwerty", "Frobnicate Widget"], "related": []})

    report = ask(
        "quantum pancake zebra",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=HashEmbedder(),
    )
    assert '"Zzyzx Qwerty"' in report.text
    assert '"Frobnicate Widget"' in report.text
    assert "may still hold" in report.text
    assert "has nothing" not in report.text
