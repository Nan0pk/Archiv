from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from memory_support import FakeModel, HashEmbedder, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.memory.ask import (
    NOTHING_FOUND,
    ask,
    check_report,
    entry_prompt,
    exit_prompt,
    parse_asked,
    parse_entry,
    question_terms,
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


def _walk(home: Path, forms: list[str], hops: int = 2, **options: Any) -> list[Passage]:
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


def test_question_terms_keep_the_subject_and_drop_words_that_only_describe_asking() -> None:
    terms = question_terms("What does the collection hold about the Pier Guild?")
    assert "pier guild" in terms
    assert "pier" in terms
    assert "guild" in terms
    for word in ("collection", "hold", "what", "the"):
        assert word not in terms
    assert question_terms("What does the collection hold?") == []
    assert question_terms("   ") == []
    # a differently worded question gives its own subject, not a fixed template
    assert "river bank" in question_terms("Tell me about the river bank in spring")


def _own_words_archive(tmp_path: Path, own: int = 12, other: int = 3) -> Path:
    """`own` passages use the asked-for name; `other` use only a second form of it."""
    docs = {
        f"o{i}.txt": f"The Orchard Society met on day {i} to plan the new harvest festival."
        for i in range(own)
    }
    docs.update(
        {
            f"x{i}.txt": f"The OS Group opened its store room on day {i} for the winter stock."
            for i in range(other)
        }
    )
    return make_archive(tmp_path, docs)


def test_walk_searches_the_questions_own_words_when_the_first_step_leaves_them_out(
    tmp_path: Path,
) -> None:
    home = _own_words_archive(tmp_path)
    without = _walk(home, ["Zzyzx"], hops=0)
    assert without == []
    own = question_terms("What does the collection hold about the Orchard Society?")
    got = _walk(home, ["Zzyzx"], hops=0, own=own)
    assert len(got) == 12
    assert all("Orchard Society" in p.text for p in got)


def test_the_questions_own_words_do_not_crowd_out_the_first_steps_other_forms(
    tmp_path: Path,
) -> None:
    home = _own_words_archive(tmp_path)
    got = _walk(home, ["OS Group"], hops=0, own=["orchard society"], max_passages=8)
    assert len(got) == 8
    # each side gets its own quota of the exact-match half, so neither is shut out
    assert sum("OS Group" in p.text for p in got) >= 2
    assert sum("Orchard Society" in p.text for p in got) >= 4


def test_a_quota_the_question_words_do_not_use_goes_to_the_first_steps_forms(
    tmp_path: Path,
) -> None:
    home = _own_words_archive(tmp_path, own=1, other=6)
    got = _walk(home, ["OS Group"], hops=0, own=["orchard society"], max_passages=8)
    assert sum("Orchard Society" in p.text for p in got) == 1
    assert sum("OS Group" in p.text for p in got) == 6


def test_a_word_or_phrase_found_in_too_many_passages_is_not_used_as_an_own_word(
    tmp_path: Path,
) -> None:
    home = _own_words_archive(tmp_path)
    # "society" is in 12 passages, more than the limit of 5, so it says nothing
    assert _walk(home, ["Zzyzx"], hops=0, own=["society"], own_limit=5) == []
    assert _walk(home, ["Zzyzx"], hops=0, own=["orchard society"], own_limit=5) == []
    assert len(_walk(home, ["Zzyzx"], hops=0, own=["orchard society"], own_limit=50)) == 12


def test_the_questions_own_words_must_be_held_together_not_one_by_one(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path,
        {
            "a.txt": "The harbour pilots met at the old pier before the storm season began.",
            "b.txt": "Harbour fees rose again this spring across all of the northern ports.",
            "c.txt": "The pilots trained twelve new recruits during the long winter months.",
            "d.txt": "Harbour fees were settled in full by the senior pilots last autumn.",
        },
    )
    # the two words side by side: only the passage that has them side by side counts
    together = _walk(home, ["Zzyzx"], hops=0, own=question_terms("harbour pilots"))
    assert [p.source_name for p in together] == ["a.txt"]
    # no passage has them side by side, so a passage has to hold both words anywhere
    apart = _walk(home, ["Zzyzx"], hops=0, own=["pilots harbour", "harbour", "pilots"])
    assert sorted(p.source_name for p in apart) == ["a.txt", "d.txt"]


def test_a_longer_name_needs_its_whole_phrase_not_a_common_part_of_it(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path,
        {
            "a.txt": "The Westgate Valley Water Authority inspected the northern pumping station.",
            "b.txt": "The Westgate Valley Water Board spoke to the press about the closure.",
            "c.txt": "The Eastgate Valley Water Authority was named the new owner of the station.",
        },
    )
    own = question_terms("Westgate Valley Water Authority")
    assert [p.source_name for p in _walk(home, ["Zzyzx"], hops=0, own=own)] == ["a.txt"]


def test_a_window_that_is_in_too_many_passages_is_dropped_from_what_a_long_name_needs(
    tmp_path: Path,
) -> None:
    docs = {
        f"b{i}.txt": f"The Westgate Valley Water Board met on day {i} to review the pump plans."
        for i in range(4)
    }
    docs["a.txt"] = "The Westgate Valley Water Authority inspected the northern pumping station."
    home = make_archive(tmp_path, docs)
    own = question_terms("Westgate Valley Water Authority")
    # the first three-word window is in five passages, more than the limit of three, so it is
    # not required; the full name is still found through the other window
    got = _walk(home, ["Zzyzx"], hops=0, own=own, own_limit=3)
    assert [p.source_name for p in got] == ["a.txt"]


def test_padding_words_do_not_become_the_questions_own_words_when_the_subject_is_too_common(
    tmp_path: Path,
) -> None:
    docs = {
        f"o{i}.txt": f"The Orchard Society met on day {i} to plan the new harvest festival."
        for i in range(8)
    }
    docs["n1.txt"] = "Everyone read everything on the notice board before the vote."
    docs["n2.txt"] = "She wrote down everything the guide said about the old bridge."
    home = make_archive(tmp_path, docs)
    own = question_terms("Tell me everything the documents say about the orchard society")
    # "orchard" and "society" are in 8 passages, more than the limit of 5, so they say nothing.
    # What is left, "everything", is rarer than they are, but it is padding, not the subject:
    # nothing is used and the walk goes on as it did without the question's words.
    assert _walk(home, ["Zzyzx"], hops=0, own=own, own_limit=5) == []


def test_the_entry_prompt_asks_for_the_question_words_that_name_the_subject() -> None:
    prompt = entry_prompt("Tell me everything about the Orchard Society")
    assert '"asked"' in prompt
    assert "without changing a letter" in prompt


def test_asked_words_count_only_when_they_are_in_the_question_word_for_word() -> None:
    question = "Tell me everything the documents say about the  Orchard   Society?"
    ok = parse_asked(json.dumps({"forms": [], "asked": "orchard society"}), question)
    assert ok == "Orchard Society"  # the question's own spelling, spacing tidied
    assert parse_asked(json.dumps({"asked": '"Orchard Society"'}), question) == "Orchard Society"
    # not in the question, a fragment of a word, wrong type, empty, missing, not JSON
    assert parse_asked(json.dumps({"asked": "Pier Guild"}), question) == ""
    assert parse_asked(json.dumps({"asked": "Orchard Soc"}), question) == ""
    assert parse_asked(json.dumps({"asked": ["Orchard Society"]}), question) == ""
    assert parse_asked(json.dumps({"asked": "  "}), question) == ""
    assert parse_asked(json.dumps({"forms": ["a"]}), question) == ""
    assert parse_asked("no json here", question) == ""
    # a name that ends in punctuation is kept as written
    assert parse_asked(json.dumps({"asked": "JuA."}), "What is JuA. in the files?") == "JuA."


def test_ask_searches_the_asked_words_when_padding_hides_the_subject(tmp_path: Path) -> None:
    docs = {
        f"o{i}.txt": f"The Orchard Society met on day {i} to plan the new harvest festival."
        for i in range(8)
    }
    docs["n1.txt"] = "Everyone read everything on the notice board before the vote."
    docs["n2.txt"] = "She wrote down everything the guide said about the old bridge."
    home = make_archive(tmp_path, docs)
    question = "Tell me everything the documents say about the orchard society"
    reports: dict[str, list[str]] = {}

    def run(answer: dict[str, object]) -> list[str]:
        prompts: list[str] = []

        def complete(prompt: str) -> str:
            prompts.append(prompt)
            if '"forms"' in prompt:
                return json.dumps(answer)
            return "Noted [P1]."

        ask(question, home=home, complete=complete, model_label=str(answer), embedder=None)
        return prompts

    with_asked = run({"forms": ["Zzyzx"], "related": [], "asked": "orchard society"})
    without = run({"forms": ["Zzyzx"], "related": []})
    reports["with"], reports["without"] = with_asked, without
    # with the asked words the report step is given the orchard passages; without them the
    # padding words find nothing and the first step's one useless form finds nothing either
    assert len(with_asked) == 2
    assert "Orchard Society met" in with_asked[1]
    assert len(without) == 1  # nothing found, so no report step


def test_without_own_word_hits_the_walk_is_unchanged(tmp_path: Path) -> None:
    home = _guild_archive(tmp_path)
    plain = _walk(home, ["Pier Guild"], hops=1, max_passages=8, text_limit=20)
    with_words = _walk(
        home,
        ["Pier Guild"],
        hops=1,
        max_passages=8,
        text_limit=20,
        own=["no such words anywhere"],
    )
    assert [p.segment_id for p in with_words] == [p.segment_id for p in plain]


def test_ask_searches_the_questions_own_words_even_when_the_first_step_misses_them(
    tmp_path: Path,
) -> None:
    home = _own_words_archive(tmp_path, own=3, other=0)
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if "Answer with JSON only" in prompt:
            return json.dumps({"forms": ["Zzyzx"], "related": []})
        return "The society met several times [P1]."

    report = ask(
        "Tell me about the Orchard Society",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=None,
    )
    assert not report.text.startswith(NOTHING_FOUND)
    assert len(prompts) == 2
    assert "Orchard Society" in prompts[1]


def test_own_word_matches_that_do_not_fit_take_turns_with_graph_and_meaning_passages(
    tmp_path: Path,
) -> None:
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
            max_passages=8,
            text_limit=20,
            own=["yard"],  # found in 6 of 9 passages here: far more than the places left over
        )
    assert len(got) == 8
    own_matches = sum("yard" in p.text for p in got)
    other = len(got) - own_matches
    assert own_matches >= 4
    assert other >= 2  # the passages reached through meaning still get places
