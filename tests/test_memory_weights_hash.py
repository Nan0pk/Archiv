from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from memory_support import HashEmbedder, make_archive

from archiv.memory.build import embed_passages, passage_vectors_base
from archiv.memory.passages import load_passages
from archiv.memory.vectors import FastEmbedder, VectorIndex, vectors_match
from archiv.storage.layout import ArchivLayout

DOCS = {
    "a.txt": "The Orchard Society met on a quiet day to plan the new harvest festival.",
    "b.txt": "The Harbour Pilots Union met on a windy day to plan the new winter timetable.",
}


class Weighted(HashEmbedder):
    def __init__(self, weights: str) -> None:
        super().__init__()
        self.weights = weights


def test_the_hash_of_the_weight_files_is_saved_with_the_vectors(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    layout = ArchivLayout.resolve(home)
    embed_passages(layout, load_passages(home), Weighted("aaa"))
    meta = json.loads(Path(f"{passage_vectors_base(layout)}.ids.json").read_text("utf-8"))
    assert meta["weights"] == "aaa"
    assert VectorIndex.load(passage_vectors_base(layout)).weights == "aaa"


def test_vectors_from_different_weights_under_the_same_name_are_not_reused(
    tmp_path: Path,
) -> None:
    home = make_archive(tmp_path, DOCS)
    layout = ArchivLayout.resolve(home)
    passages = load_passages(home)
    embed_passages(layout, passages, Weighted("aaa"))
    stored = VectorIndex.load(passage_vectors_base(layout))
    assert vectors_match(stored, Weighted("aaa"))
    assert not vectors_match(stored, Weighted("bbb"))
    calls: list[int] = []

    class Counting(Weighted):
        def embed(self, texts: list[str]):  # type: ignore[no-untyped-def]
            calls.append(len(texts))
            return super().embed(texts)

    embed_passages(layout, passages, Counting("bbb"))
    assert calls == [len(passages)]  # everything was embedded again
    assert VectorIndex.load(passage_vectors_base(layout)).weights == "bbb"


def test_vectors_saved_before_hashes_were_recorded_are_still_accepted(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    layout = ArchivLayout.resolve(home)
    passages = load_passages(home)
    embed_passages(layout, passages, HashEmbedder())  # no weights attribute at all
    stored = VectorIndex.load(passage_vectors_base(layout))
    assert stored.weights == ""
    assert vectors_match(stored, Weighted("aaa"))  # an old index is reused, as before
    assert vectors_match(
        VectorIndex(ids=[], vectors=stored.vectors, embedder=stored.embedder, weights="aaa"),
        HashEmbedder(),
    )


def test_a_different_embedder_name_never_matches(tmp_path: Path) -> None:
    home = make_archive(tmp_path, DOCS)
    layout = ArchivLayout.resolve(home)
    embed_passages(layout, load_passages(home), Weighted("aaa"))
    stored = VectorIndex.load(passage_vectors_base(layout))
    other = Weighted("aaa")
    other.name = "another-model"
    assert not vectors_match(stored, other)


def _fast_embedder_on(folder: Path | None) -> FastEmbedder:
    """A FastEmbedder over a folder of files, without the library: only `weights` is used."""
    embedder = object.__new__(FastEmbedder)
    model = SimpleNamespace() if folder is None else SimpleNamespace(_model_dir=str(folder))
    embedder.__dict__.update({"_weights": None, "_model": SimpleNamespace(model=model)})
    return embedder


def test_the_weights_hash_covers_the_onnx_files_and_not_the_rest(tmp_path: Path) -> None:
    folder = tmp_path / "model"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.onnx").write_bytes(b"first")
    (folder / "sub" / "b.onnx").write_bytes(b"second")
    (folder / "tokenizer.json").write_text("{}")
    expected = hashlib.sha256()
    for name, data in (("a.onnx", b"first"), ("b.onnx", b"second")):
        expected.update(name.encode() + data)
    assert _fast_embedder_on(folder).weights == expected.hexdigest()
    (folder / "tokenizer.json").write_text('{"changed": true}')
    assert _fast_embedder_on(folder).weights == expected.hexdigest()  # other files do not count
    (folder / "a.onnx").write_bytes(b"first, edited")
    assert _fast_embedder_on(folder).weights != expected.hexdigest()


def test_an_embedder_whose_files_cannot_be_found_records_no_hash() -> None:
    assert _fast_embedder_on(None).weights == ""
