from __future__ import annotations

from pathlib import Path

import numpy as np
from memory_support import FakeModel, HashEmbedder, make_archive

from archiv.memory.build import build_memory
from archiv.memory.vectors import VectorIndex

DOCS = {
    "a.txt": "The Harbour Board approved the Bridge Plan after a long debate.",
    "b.txt": "The Orchard Group asked the Harbour Board for a meeting next spring.",
}


def _index() -> VectorIndex:
    vectors = np.array([[1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32)
    return VectorIndex(ids=["x", "y", "z"], vectors=vectors, embedder="t")


def test_nearest_orders_and_floors() -> None:
    query = np.array([1, 0], dtype=np.float32)
    hits = _index().nearest(query, k=3, floor=0.5)
    assert [i for i, _ in hits] == ["x", "y"]
    assert hits[0][1] > hits[1][1] >= 0.5
    assert [i for i, _ in _index().nearest(query, k=3, floor=0.5, exclude={"x"})] == ["y"]


def test_save_load_roundtrip(tmp_path: Path) -> None:
    base = tmp_path / "vec"
    _index().save(base)
    loaded = VectorIndex.load(base)
    assert loaded.ids == ["x", "y", "z"]
    assert loaded.embedder == "t"
    assert np.allclose(loaded.vectors, _index().vectors)


def test_build_skips_reembed_when_unchanged(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    embedder = HashEmbedder()
    build_memory(home=home, complete=FakeModel([]), model_label="f", embedder=embedder)
    assert embedder.calls == 1
    build_memory(home=home, complete=FakeModel([]), model_label="f", embedder=embedder)
    assert embedder.calls == 1


def test_archiv_starts_without_numpy() -> None:
    """The memory extra is optional: the main command must start when numpy is absent."""
    import subprocess
    import sys

    code = "import sys; sys.modules['numpy'] = None; import archiv.cli"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-400:]
