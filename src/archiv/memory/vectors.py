"""Meaning vectors: embedding text on the CPU and finding nearest neighbours."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import dataclass, field
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


def weights_of(embedder: object) -> str:
    """The recorded hash of an embedder's weight files, or "" when it has none to record."""
    return str(getattr(embedder, "weights", "") or "")


def vectors_match(stored: VectorIndex, embedder: Embedder) -> bool:
    """Whether vectors on disk were made by this embedder.

    The name has to be the same. When both sides recorded a hash of the weight files it has to be
    the same too, so a different build of the same model is not mixed in. Vectors saved before
    hashes were recorded carry none and are accepted, as they always were.
    """
    if stored.embedder != embedder.name:
        return False
    current = weights_of(embedder)
    return not stored.weights or not current or stored.weights == current


def embed_folders() -> list[Path]:
    """The folders the embedding model files are looked for in, in order.

    `ARCHIV_EMBED_CACHE` names the only one when it is set. Otherwise it is the user's cache
    folder, which stays across restarts, and then the earlier default under /tmp, which many
    systems empty on every restart.
    """
    named = os.environ.get("ARCHIV_EMBED_CACHE")
    if named:
        return [Path(named)]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return [cache / "archiv" / "fastembed", Path("/tmp/fastembed_cache")]


class FastEmbedder:
    """CPU embedder. It never downloads weights: they must already be on disk."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: Path | None = None) -> None:
        self.name = model_name
        self._weights: str | None = None
        folders = [cache_dir] if cache_dir else embed_folders()
        try:
            # Loaded by name so that type checking works without the optional library.
            fastembed = importlib.import_module("fastembed")
        except ImportError as error:
            raise EmbedderUnavailable(
                "The meaning-vector library is not installed. "
                "Install it with: pip install 'archiv-core[memory]'"
            ) from error
        failure: Exception | None = None
        for folder in folders:
            try:
                self._model = fastembed.TextEmbedding(
                    model_name, cache_dir=str(folder), local_files_only=True
                )
                break
            except Exception as error:
                failure = error
        else:
            looked = " and ".join(str(folder) for folder in folders)
            raise EmbedderUnavailable(
                f"The embedding model '{model_name}' is not on this machine (looked in {looked}). "
                f"Archiv never downloads models by itself. Place the model in {folders[0]} first."
            ) from failure

    @property
    def weights(self) -> str:
        """SHA-256 over the model's .onnx weight files (in name order), computed once."""
        if self._weights is None:
            digest = hashlib.sha256()
            try:
                folder = Path(self._model.model._model_dir)  # type: ignore[attr-defined]
                for path in sorted(folder.rglob("*.onnx")):
                    digest.update(path.name.encode("utf-8"))
                    with path.resolve().open("rb") as handle:
                        for block in iter(lambda: handle.read(1 << 20), b""):
                            digest.update(block)
                self._weights = digest.hexdigest()
            except (AttributeError, OSError):
                self._weights = ""  # not recorded: vectors are then matched by name alone
        return self._weights

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
    weights: str = ""
    _row: dict[str, int] | None = field(default=None, init=False, repr=False, compare=False)

    def scores(self, ids: list[str], query: Vectors) -> dict[str, float]:
        """Similarity of each stored passage in `ids` to `query`; unknown ids are left out."""
        if self._row is None:
            self._row = {identifier: position for position, identifier in enumerate(self.ids)}
        known = [identifier for identifier in ids if identifier in self._row]
        if not known:
            return {}
        values = self.vectors[[self._row[identifier] for identifier in known]] @ query
        return {identifier: float(value) for identifier, value in zip(known, values, strict=True)}

    def save(self, base: Path) -> None:
        base.parent.mkdir(parents=True, exist_ok=True)
        import numpy as np

        np.save(f"{base}.npy", self.vectors)
        Path(f"{base}.ids.json").write_text(
            json.dumps({"ids": self.ids, "embedder": self.embedder, "weights": self.weights}),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, base: Path) -> VectorIndex:
        import numpy as np

        meta = json.loads(Path(f"{base}.ids.json").read_text(encoding="utf-8"))
        return cls(
            ids=list(meta["ids"]),
            vectors=np.load(f"{base}.npy"),
            embedder=str(meta.get("embedder", "")),
            weights=str(meta.get("weights", "")),
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
