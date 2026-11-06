"""Meaning vectors: embedding text on the CPU and finding nearest neighbours."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

# numpy belongs to the optional `memory` extra, so it is imported only where it is used.
# Importing it at the top would make every `archiv` command depend on it.
if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    Vectors = NDArray[np.float32]

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class EmbedderUnavailable(RuntimeError):
    """The embedding library or its weights are not installed on this machine."""


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> Vectors:
        """Return one L2-normalised row per text."""
        ...


class FastEmbedder:
    """CPU embedder. It never downloads weights: they must already be on disk."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: Path | None = None) -> None:
        self.name = model_name
        folder = cache_dir or Path(os.environ.get("ARCHIV_EMBED_CACHE", "/tmp/fastembed_cache"))
        try:
            from fastembed import TextEmbedding
        except ImportError as error:
            raise EmbedderUnavailable(
                "The meaning-vector library is not installed. "
                "Install it with: pip install 'archiv-core[memory]'"
            ) from error
        try:
            self._model = TextEmbedding(model_name, cache_dir=str(folder), local_files_only=True)
        except Exception as error:
            raise EmbedderUnavailable(
                f"The embedding model '{model_name}' is not on this machine (looked in {folder}). "
                "Archiv never downloads models by itself. Place the model there first."
            ) from error

    def embed(self, texts: list[str]) -> Vectors:
        import numpy as np

        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        rows = np.array(
            list(self._model.embed([texts[i] for i in order], batch_size=64)), dtype=np.float32
        )
        out = np.empty_like(rows)
        out[order] = rows
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


@dataclass
class VectorIndex:
    ids: list[str]
    vectors: Vectors
    embedder: str = ""

    def save(self, base: Path) -> None:
        base.parent.mkdir(parents=True, exist_ok=True)
        import numpy as np

        np.save(f"{base}.npy", self.vectors)
        Path(f"{base}.ids.json").write_text(
            json.dumps({"ids": self.ids, "embedder": self.embedder}), encoding="utf-8"
        )

    @classmethod
    def load(cls, base: Path) -> VectorIndex:
        import numpy as np

        meta = json.loads(Path(f"{base}.ids.json").read_text(encoding="utf-8"))
        return cls(
            ids=list(meta["ids"]),
            vectors=np.load(f"{base}.npy"),
            embedder=str(meta.get("embedder", "")),
        )

    def nearest(
        self,
        query: Vectors,
        k: int,
        floor: float,
        exclude: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        import numpy as np

        scores = self.vectors @ query
        hits: list[tuple[str, float]] = []
        for position in np.argsort(-scores):
            score = float(scores[position])
            if score < floor or len(hits) >= k:
                break
            identifier = self.ids[int(position)]
            if exclude and identifier in exclude:
                continue
            hits.append((identifier, score))
        return hits
