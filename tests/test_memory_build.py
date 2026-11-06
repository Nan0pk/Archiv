from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from memory_support import FakeModel, HashEmbedder, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.memory.build import build_memory
from archiv.memory.store import memory_path, open_memory
from archiv.search.index import search_index_path
from archiv.storage.layout import ArchivLayout

SHARED_TAIL = (
    " met on the old quay to discuss the repair schedule for the winter months and the"
    " budget for the spring works along the river bank near the mill."
)
FORMS = ["Harbour Board", "Bridge Plan", "Orchard Group"]
DOCS = {
    "a.txt": "The Harbour Board approved the Bridge Plan after a long debate.",
    "b.txt": "The Orchard Group asked the Harbour Board for a meeting next spring.",
}


def _counts(home: Path) -> tuple[int, int]:
    with open_memory(ArchivLayout.resolve(home)) as conn:
        things = conn.execute("SELECT COUNT(*) FROM things").fetchone()[0]
        relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    return int(things), int(relations)


def test_build_extracts_and_stores(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    summary = build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    assert summary.documents == 2
    assert summary.failed_batches == 0
    assert _counts(home) == (3, 2)
    layout = ArchivLayout.resolve(home)
    index = sqlite3.connect(search_index_path(layout))
    known = {r[0] for r in index.execute("SELECT segment_id FROM segments")}
    with open_memory(layout) as conn:
        stored = {r[0] for r in conn.execute("SELECT segment_id FROM forms")}
    assert stored and stored <= known


def test_rerun_makes_zero_calls(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    first = build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    second = build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    assert first.model_calls > 0
    assert second.model_calls == 0
    assert second.cache_hits == first.model_calls
    assert _counts(home) == (3, 2)


def test_label_change_recalls(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    again = build_memory(home=home, complete=FakeModel(FORMS), model_label="other")
    assert again.model_calls > 0


def test_failed_batch_does_not_stop_build(tmp_path: Path) -> None:
    docs = dict(DOCS)
    docs["c.txt"] = "FAILME this passage makes the stand-in model refuse to answer."
    home = make_archive(tmp_path, docs)
    summary = build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    assert summary.failed_batches == 1
    assert summary.documents == 3
    assert _counts(home) == (3, 2)


def test_unicode_identity(tmp_path: Path) -> None:
    composed, decomposed = "Café Board", "Café Board"
    home = make_archive(
        tmp_path,
        {
            "a.txt": f"The {composed} met on the first Monday of the month.",
            "b.txt": f"Minutes of the {decomposed} were filed the following week.",
        },
    )
    build_memory(home=home, complete=FakeModel([composed, decomposed]), model_label="fake")
    with open_memory(ArchivLayout.resolve(home)) as conn:
        things = conn.execute("SELECT COUNT(*) FROM things").fetchone()[0]
        forms = {r[0] for r in conn.execute("SELECT DISTINCT form FROM forms")}
    assert things == 1
    assert forms == {composed, decomposed}


def test_cli_build_json(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    script = 'import sys; sys.stdin.read(); print(\'{"things": [], "relations": []}\')'
    command = f'{sys.executable} -c "{script}"'
    result = CliRunner().invoke(
        app,
        [
            "memory",
            "build",
            "--home",
            str(home),
            "--model-cmd",
            command,
            "--model-label",
            "echo",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["documents"] == 2
    assert memory_path(ArchivLayout.resolve(home)).is_file()


def test_cli_build_limits_merge_questions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("archiv.memory.cli._embedder", lambda: HashEmbedder())
    home = make_archive(
        tmp_path,
        {
            "a.txt": f"Quay Committee{SHARED_TAIL}",
            "b.txt": f"Harbour Panel{SHARED_TAIL}",
        },
    )
    log = tmp_path / "calls.log"
    script = tmp_path / "model.py"
    script.write_text(
        "import sys, json, re\n"
        f"open({str(log)!r}, 'a').write('call\\n')\n"
        "p = sys.stdin.read()\n"
        "if 'same thing' in p:\n"
        "    print(json.dumps({'same': True, 'reason': 'x'}))\n"
        "else:\n"
        "    t = [{'form': ' '.join(m[1].split()[:2]), 'kind': 'k', 'passages': [m[0]]}\n"
        "         for m in re.findall(r'^\\[(P\\d+)\\] (.*)$', p, flags=re.M)]\n"
        "    print(json.dumps({'things': t, 'relations': []}))\n",
        encoding="utf-8",
    )

    def run(label: str, limit: int) -> int:
        log.write_text("", encoding="utf-8")
        result = CliRunner().invoke(
            app,
            [
                "memory",
                "build",
                "--home",
                str(home),
                "--model-cmd",
                f"{sys.executable} {script}",
                "--model-label",
                label,
                "--max-merge-pairs",
                str(limit),
            ],
        )
        assert result.exit_code == 0, result.output
        return len(log.read_text(encoding="utf-8").splitlines())

    assert run("none", 0) < run("some", 5)
