from __future__ import annotations

import sys
from pathlib import Path

import pytest
from memory_support import make_archive

from archiv.memory.model import command_completer
from archiv.memory.passages import count_literal_passages, load_passages, passages_by_object

SHARED = "The harbour committee reviewed the second bridge proposal in detail."


def test_load_passages_dedupes_and_filters(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path,
        {
            "one.txt": f"{SHARED}\n\nShort line\n\nThe orchard group planted four rows of pears.",
            "two.txt": f"{SHARED}\n\nThe weaving guild opened a second workshop near the mill.",
        },
    )
    passages = load_passages(home)
    texts = [p.text for p in passages]
    assert texts.count(SHARED) == 1
    assert not any(t == "Short line" for t in texts)
    assert any("orchard" in t for t in texts)
    assert any("weaving" in t for t in texts)
    assert all(len(p.segment_id) == 64 and len(p.object_sha256) == 64 for p in passages)
    grouped = passages_by_object(passages)
    assert sum(len(v) for v in grouped.values()) == len(passages)


def test_load_passages_requires_index(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_passages(tmp_path / "empty-home")


def test_command_completer_pipes_stdin() -> None:
    complete = command_completer(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"]
    )
    assert complete("abc") == "ABC\n"


def test_count_literal_passages_counts_distinct_passages(tmp_path: Path) -> None:
    home = make_archive(
        tmp_path,
        {
            "a.txt": (
                f"{SHARED}\n\nShort line\n\nHARBOUR committee met again on the first day of spring."
            ),
            "b.txt": f"{SHARED}\n\nThe orchard group planted four rows of pears.",
        },
    )
    assert count_literal_passages(home, "harbour committee") == 2  # case-insensitive, repeats once
    assert count_literal_passages(home, "orchard group") == 1
    assert count_literal_passages(home, "no such thing") == 0
