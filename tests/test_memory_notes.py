"""A question says, in a sentence, when its answer had less to work with than it should."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from memory_support import FakeModel, HashEmbedder, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.ingestion import ingest_file
from archiv.memory.ask import Report, ask
from archiv.memory.build import build_memory, build_memory_offline
from archiv.memory.model import Complete
from archiv.memory.store import open_memory
from archiv.search.index import rebuild_search_index
from archiv.storage.layout import ArchivLayout

FORMS = ["Alpha Works", "Beta Trust"]
DOCS = {
    "a.txt": "Alpha Works signed an agreement with Beta Trust over the northern lease.",
    "b.txt": "Beta Trust later transferred the northern lease to a society for a fee.",
}
REPORT = "Alpha Works signed an agreement with Beta Trust [P1]."


def _model(prompt: str) -> str:
    if '"forms"' in prompt:
        return json.dumps({"forms": ["Alpha Works"], "related": []})
    return REPORT


def _ask(home: Path, embedder: HashEmbedder | None = None, complete: Complete = _model) -> Report:
    return ask(
        "What do we know about Alpha Works?",
        home=home,
        complete=complete,
        model_label="fake",
        embedder=embedder,
    )


def _one(report: Report, words: str) -> None:
    assert len(report.notes) == 1, report.notes
    assert words in report.notes[0], report.notes


def test_a_memory_that_is_built_and_current_gives_no_note(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home, embedder=HashEmbedder())
    assert _ask(home, HashEmbedder()).notes == []


def test_a_question_before_any_build_says_the_memory_was_not_built(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    report = _ask(home)
    assert report.text == REPORT  # exact words still find the passage
    _one(report, "has not been built")


def test_documents_added_after_the_build_are_reported(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake", embedder=HashEmbedder())
    late = tmp_path / "source" / "late.txt"
    late.write_text("Alpha Works opened a second office in the valley this year.", encoding="utf-8")
    ingest_file(late, home=home)
    rebuild_search_index(home=home)
    _one(_ask(home, HashEmbedder()), "added or changed after the memory was built")
    build_memory_offline(home=home, embedder=HashEmbedder())
    assert _ask(home, HashEmbedder()).notes == []


def test_a_memory_from_before_the_record_was_kept_says_it_cannot_be_checked(
    tmp_path: Path,
) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake", embedder=HashEmbedder())
    with open_memory(ArchivLayout.resolve(home)) as conn:
        conn.execute("DELETE FROM outputs WHERE key NOT GLOB '[0-9a-f]*'")
    _one(_ask(home, HashEmbedder()), "cannot be checked")


def test_a_build_without_meaning_vectors_is_reported(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home)
    _one(_ask(home), "no meaning vectors")


def test_vectors_that_cannot_be_used_are_reported(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home, embedder=HashEmbedder())
    _one(_ask(home, None), "could not be loaded")
    other = HashEmbedder()
    other.name = "another-model"
    _one(_ask(home, other), "different embedding model")


def test_passages_without_a_vector_are_counted(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home, embedder=HashEmbedder())
    late = tmp_path / "source" / "late.txt"
    late.write_text("Alpha Works opened a second office in the valley this year.", encoding="utf-8")
    ingest_file(late, home=home)
    rebuild_search_index(home=home)
    build_memory_offline(home=home)  # the names are current again, the vectors are not
    _one(
        _ask(home, HashEmbedder()),
        "1 passages have no meaning vector, so what they say may be missing",
    )


def test_a_first_answer_that_cannot_be_read_is_reported(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home, embedder=HashEmbedder())

    def complete(prompt: str) -> str:
        return "sorry, no" if '"forms"' in prompt else REPORT

    _one(_ask(home, HashEmbedder(), complete), "first answer could not be read")


def test_an_empty_report_is_reported_and_not_kept(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory_offline(home=home, embedder=HashEmbedder())
    answers = iter(["  \n", REPORT])

    def complete(prompt: str) -> str:
        if '"forms"' in prompt:
            return json.dumps({"forms": ["Alpha Works"], "related": []})
        return next(answers)

    first = _ask(home, HashEmbedder(), complete)
    assert first.text == ""
    _one(first, "returned no report text")
    second = _ask(home, HashEmbedder(), complete)  # asked again, not answered from the empty one
    assert second.text == REPORT
    assert second.notes == []


def test_a_plain_build_says_when_merges_from_a_model_are_still_in_force(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    assert build_memory_offline(home=home).earlier_merges == 0
    with open_memory(ArchivLayout.resolve(home)) as conn:
        conn.execute("INSERT INTO merges VALUES (1, 2, 1, 'a model said so', '[]')")
    assert build_memory_offline(home=home).earlier_merges == 1
    result = CliRunner().invoke(app, ["memory", "build", "--home", str(home)])
    assert result.exit_code == 0, result.output
    assert "1 pairs of written forms that a model confirmed" in result.output


def _cli_ask(home: Path, command: str, env: dict[str, str] | None = None, *more: str):  # noqa: ANN202
    arguments = ["memory", "ask", "What about Alpha Works?", "--home", str(home), *more]
    return CliRunner().invoke(app, arguments, env={"ARCHIV_MODEL_CMD": command, **(env or {})})


def test_cli_prints_notes_and_takes_the_model_name_from_the_environment(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    script = tmp_path / "model.py"
    script.write_text(
        "import sys, json; p = sys.stdin.read()\n"
        "print(json.dumps({'forms': ['Alpha Works'], 'related': []}) if '\"forms\"' in p "
        f"else {REPORT!r})",
        encoding="utf-8",
    )
    command = f"{sys.executable} {script}"
    named = {"ARCHIV_MODEL_LABEL": "from-env"}
    result = _cli_ask(home, command, named)
    assert result.exit_code == 0, result.output
    assert "Note: The memory has not been built" in result.output
    as_json = _cli_ask(home, command, named, "--json")
    assert "has not been built" in json.loads(as_json.stdout)["notes"][0]
    assert _cli_ask(home, command).exit_code == 2  # no name given anywhere


def test_cli_says_in_a_sentence_when_the_model_command_fails(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    script = tmp_path / "broken.py"
    script.write_text("import sys; sys.exit('out of credit')", encoding="utf-8")
    failed = _cli_ask(home, f"{sys.executable} {script}", None, "--model-label", "x")
    assert failed.exit_code == 1
    assert "The model command failed: it ended with exit code 1" in failed.output
    assert "out of credit" in failed.output
    assert "Traceback" not in failed.output
    missing = _cli_ask(home, str(tmp_path / "no-such-program"), None, "--model-label", "x")
    assert missing.exit_code == 1
    assert "could not be started" in missing.output
