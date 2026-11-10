from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from memory_support import HashEmbedder, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.memory.build import build_memory_offline
from archiv.memory.offline import offline_extractions
from archiv.memory.passages import load_passages
from archiv.memory.store import memory_path
from archiv.storage.layout import ArchivLayout

FILLER = "The group met on the day to plan the harvest festival with everyone"


def _docs() -> dict[str, str]:
    """40 documents of padding, with a few names that repeat and one that does not."""
    verbs = ["said", "wrote", "asked", "left", "ruled", "spoke"]
    docs = {f"d{i:02d}.txt": f"{FILLER} number{i:02d}." for i in range(40)}
    for i in range(5):
        docs[f"d{i:02d}.txt"] += f" Orchard Society {verbs[i]} item{i:02d}."
    for i in range(3, 6):
        docs[f"d{i:02d}.txt"] += f" Harbour Pilots {verbs[i]} thing{i:02d}."
    docs["d20.txt"] += " Zorblax appeared once."
    arabic = ["\u0627\u062c\u062a\u0645\u0639", "\u0642\u0631\u0631", "\u062d\u0636\u0631"]
    for i, verb in zip((30, 31, 32), arabic, strict=True):
        docs[f"d{i:02d}.txt"] += (
            f" \u0645\u062c\u0644\u0633 \u0627\u0644\u0645\u064a\u0646\u0627\u0621 {verb}."
        )
    return docs


def _forms(tmp_path: Path) -> tuple[set[str], list[tuple[str, str, str]]]:
    home = make_archive(tmp_path, _docs())
    extractions = offline_extractions(load_passages(home))
    forms = {m.form for e in extractions.values() for m in e.mentions}
    relations = [(r.source, r.phrase, r.target) for e in extractions.values() for r in e.relations]
    return forms, relations


def test_repeated_names_are_found_without_a_word_list(tmp_path: Path) -> None:
    forms, _ = _forms(tmp_path)
    assert "Orchard Society" in forms
    assert "Harbour Pilots" in forms


def test_words_that_are_everywhere_and_names_in_one_document_are_left_out(tmp_path: Path) -> None:
    forms, _ = _forms(tmp_path)
    assert "Zorblax" not in forms  # in one document only: it connects nothing
    lowered = {form.casefold() for form in forms}
    for filler in ("the", "group", "harvest festival", "met on the day"):
        assert filler not in lowered  # in every passage, so it says nothing


def test_a_script_without_capital_letters_works_the_same_way(tmp_path: Path) -> None:
    forms, _ = _forms(tmp_path)
    assert "مجلس الميناء" in forms


def test_names_in_the_same_passage_are_connected(tmp_path: Path) -> None:
    _, relations = _forms(tmp_path)
    pairs = {frozenset((a, b)) for a, _, b in relations}
    assert frozenset(("Orchard Society", "Harbour Pilots")) in pairs
    assert all(phrase == "appears with" for _, phrase, _ in relations)


def test_a_phrase_inside_a_longer_one_found_in_the_same_documents_is_dropped(
    tmp_path: Path,
) -> None:
    forms, _ = _forms(tmp_path)
    assert "Orchard" not in forms  # it only ever appears as part of "Orchard Society"


def test_the_offline_build_needs_no_model_and_stores_things_and_vectors(tmp_path: Path) -> None:
    home = make_archive(tmp_path, _docs())
    summary = build_memory_offline(home=home, embedder=HashEmbedder())
    assert summary.model_calls == 0
    assert summary.things > 0
    assert summary.relations > 0
    layout = ArchivLayout.resolve(home)
    assert Path(f"{layout.indexes / 'memory-passages'}.ids.json").is_file()
    with sqlite3.connect(memory_path(layout)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0] == 0


def test_cli_build_without_a_model_label_uses_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archiv.memory.cli._embedder", lambda: None)
    home = make_archive(tmp_path, _docs())
    result = CliRunner().invoke(app, ["memory", "build", "--home", str(home), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["model_calls"] == 0
    assert payload["things"] > 0


def test_cli_build_refuses_a_model_command_without_a_model_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archiv.memory.cli._embedder", lambda: None)
    home = make_archive(tmp_path, _docs())
    result = CliRunner().invoke(app, ["memory", "build", "--home", str(home), "--model-cmd", "cat"])
    assert result.exit_code == 2
    assert not memory_path(ArchivLayout.resolve(home)).exists()


def _defined_docs() -> dict[str, str]:
    docs = {
        f"p{i}.txt": f"The Orchard Society hosted a market stall number {i} this year."
        for i in range(3)
    }
    docs["defines.txt"] = "The Orchard Society (OS) met to plan the new harvest festival."
    docs.update(
        {f"q{i}.txt": f"The OS opened a store room on day {i} for winter stock." for i in range(2)}
    )
    docs["noise.txt"] = (
        "See the table (see below), the year (2018), an example (e.g. this) and (approx)."
    )
    return docs


def test_a_name_the_text_defines_an_abbreviation_for_is_found(tmp_path: Path) -> None:
    from archiv.memory.offline import abbreviation_pairs

    home = make_archive(tmp_path, _defined_docs())
    found = [(long, short) for long, short, _ in abbreviation_pairs(load_passages(home))]
    assert found == [("Orchard Society", "OS")]  # brackets that are not definitions are ignored


def test_the_offline_build_stores_a_defined_abbreviation_as_a_confirmed_merge(
    tmp_path: Path,
) -> None:
    home = make_archive(tmp_path, _defined_docs())
    build_memory_offline(home=home, embedder=None)
    with sqlite3.connect(memory_path(ArchivLayout.resolve(home))) as conn:
        row = conn.execute("SELECT same, reason FROM merges").fetchone()
        forms = {r[0] for r in conn.execute("SELECT form FROM things")}
    assert row[0] == 1
    assert "Orchard Society" in row[1]
    assert {"Orchard Society", "OS"} <= forms


def test_a_question_about_an_abbreviation_also_searches_the_name_it_stands_for(
    tmp_path: Path,
) -> None:
    from archiv.memory.ask import ask

    home = make_archive(tmp_path, _defined_docs())
    build_memory_offline(home=home, embedder=None)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if '"forms"' in prompt:
            return json.dumps({"forms": ["Zzyzx"], "related": [], "asked": "OS"})
        return "Noted [P1]."

    ask(
        "What does the collection hold about OS?",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=None,
    )
    report_prompt = prompts[1]
    assert "hosted a market stall" in report_prompt  # a passage that only has the long name
    assert "opened a store room" in report_prompt  # and one that only has the abbreviation


def test_with_partners_adds_the_other_written_forms_of_a_merged_thing(tmp_path: Path) -> None:
    from archiv.memory.ask import with_partners
    from archiv.memory.store import open_memory

    home = make_archive(tmp_path, _defined_docs())
    build_memory_offline(home=home, embedder=None)
    with open_memory(ArchivLayout.resolve(home)) as conn:
        assert with_partners(conn, ["OS", "Zzyzx"]) == ["OS", "Orchard Society", "Zzyzx"]
        assert with_partners(conn, ["Zzyzx"]) == ["Zzyzx"]  # nothing known: unchanged
        assert with_partners(conn, ["os"]) == ["os", "Orchard Society"]  # form spelling is free


def test_an_abbreviation_that_stands_for_unlike_names_is_not_merged(tmp_path: Path) -> None:
    from archiv.memory.offline import abbreviation_pairs

    docs = {
        "a.txt": "The Orchard Society (OS) met to plan the new harvest festival.",
        "b.txt": "The Ocean Station (OS) met to plan the new coastal mapping.",
        "c.txt": "The Harbour Pilots Union (HPU) met to plan the new winter timetable.",
        "d.txt": "The Harbour Pilots Unions (HPU) met to plan the new spring timetable.",
    }
    found = {
        (long, short)
        for long, short, _ in abbreviation_pairs(load_passages(make_archive(tmp_path, docs)))
    }
    assert not any(short == "OS" for _, short in found)  # two different names: left alone
    assert {short for _, short in found} == {"HPU"}  # spellings of one name: kept


def test_a_rebuild_drops_a_merge_the_text_no_longer_supports(tmp_path: Path) -> None:
    home = make_archive(tmp_path, _defined_docs())
    build_memory_offline(home=home, embedder=None)
    with sqlite3.connect(memory_path(ArchivLayout.resolve(home))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM merges").fetchone()[0] == 1
    layout = ArchivLayout.resolve(home)
    with sqlite3.connect(memory_path(layout)) as conn:
        conn.execute('UPDATE merges SET reason = \'the text defines "X" as "gone"\'')
        conn.commit()
    # a stale merge written by an earlier build is removed and the current pair is written again
    build_memory_offline(home=home, embedder=None)
    with sqlite3.connect(memory_path(layout)) as conn:
        reasons = [row[0] for row in conn.execute("SELECT reason FROM merges")]
    assert reasons == ['the text defines "OS" as "Orchard Society"']
