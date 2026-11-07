from __future__ import annotations

import sys
from pathlib import Path

from memory_support import FakeModel, make_archive
from typer.testing import CliRunner

from archiv.cli import app
from archiv.memory.build import build_memory
from archiv.memory.probe import draw_probes
from archiv.memory.store import open_memory
from archiv.storage.layout import ArchivLayout

FORMS = ["Alpha Works", "Beta Trust", "Gamma Society", "Delta Guild", "Epsilon Club"]
DOCS = {
    "a.txt": "Alpha Works signed an agreement with Beta Trust over the northern lease.",
    "b.txt": "Gamma Society and Delta Guild opened a shared workshop beside the old mill.",
    "c.txt": "Epsilon Club hosted a long meeting about the yearly budget for the valley.",
}


def _home(tmp_path: Path) -> Path:
    home = make_archive(tmp_path, DOCS)
    build_memory(home=home, complete=FakeModel(FORMS), model_label="fake")
    return home


def test_probes_are_seeded_and_uniform(tmp_path: Path) -> None:
    home = _home(tmp_path)
    with open_memory(ArchivLayout.resolve(home)) as conn:
        first = draw_probes(conn, n=3, seed=5)
        again = draw_probes(conn, n=3, seed=5)
        other = draw_probes(conn, n=3, seed=6)
        everything = draw_probes(conn, n=99, seed=1)
    assert first == again
    assert first != other
    assert len({p.thing_id for p in first}) == 3
    assert len(everything) == 5
    assert first[0].question == f"What does the collection hold about {first[0].form}?"


def test_probe_cli_writes_files(tmp_path: Path) -> None:
    home = _home(tmp_path)
    script = tmp_path / "model.py"
    script.write_text(
        "import sys, json, re\n"
        "p = sys.stdin.read()\n"
        "m = re.search(r'about (.*?)\\?', p)\n"
        "form = m.group(1) if m else 'x'\n"
        "print(json.dumps({'forms': [form], 'related': []}) if '\"forms\"' in p "
        "else form + ' appears in the collection [P1].')\n",
        encoding="utf-8",
    )
    out = tmp_path / "probes"
    result = CliRunner().invoke(
        app,
        [
            "memory",
            "probe",
            "--n",
            "2",
            "--seed",
            "3",
            "--home",
            str(home),
            "--model-cmd",
            f"{sys.executable} {script}",
            "--model-label",
            "echo",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.glob("probe-*.md")) == ["probe-01.md", "probe-02.md"]
    text = (out / "probe-01.md").read_text(encoding="utf-8")
    assert "What does the collection hold about" in text
    assert "What plain search finds" in text
    judge = (out / "judge.md").read_text(encoding="utf-8")
    assert "useful?" in judge and "probe-01" in judge and "probe-02" in judge
