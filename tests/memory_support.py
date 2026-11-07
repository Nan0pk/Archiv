"""Shared helpers for the archiv.memory tests: a tiny neutral archive and fakes."""

from __future__ import annotations

from pathlib import Path

from archiv.ingestion import ingest_file
from archiv.search.index import rebuild_search_index


def make_archive(tmp_path: Path, documents: dict[str, str]) -> Path:
    """Ingest each `name -> text` as a .txt file and build the search index."""
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    for name, text in documents.items():
        path = source / name
        path.write_text(text, encoding="utf-8")
        ingest_file(path, home=home)
    rebuild_search_index(home=home)
    return home


class FakeModel:
    """A stand-in model: tags every known form found in each labelled passage.

    Two or more forms in one passage are linked with the phrase "linked to". A prompt
    containing `fail_marker` gets a non-JSON answer.
    """

    def __init__(self, forms: list[str], fail_marker: str = "FAILME") -> None:
        self.forms = forms
        self.fail_marker = fail_marker
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        import json
        import re

        self.calls += 1
        if self.fail_marker in prompt:
            return "sorry, I cannot help with that"
        things: list[dict[str, object]] = []
        relations: list[dict[str, object]] = []
        for label, text in re.findall(r"^\[(P\d+)\] (.*)$", prompt, flags=re.M):
            present = [f for f in self.forms if f in text]
            things.extend({"form": f, "kind": "a thing", "passages": [label]} for f in present)
            if len(present) >= 2:
                relations.append(
                    {
                        "source": present[0],
                        "relation": "linked to",
                        "target": present[1],
                        "passage": label,
                    }
                )
        return json.dumps({"things": things, "relations": relations})


class HashEmbedder:
    """Dependency-free stand-in embedder: hashed lowercase words in 64 dimensions."""

    name = "hash-test"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]):  # noqa: ANN201
        import hashlib

        import numpy as np

        self.calls += 1
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.casefold().split():
                digest = hashlib.sha256(word.strip(".,;:!?").encode()).digest()
                out[row, digest[0] % 64] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms
