"""Where the embedding model files are looked for."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from archiv.memory.vectors import EmbedderUnavailable, FastEmbedder, embed_folders


def _library_with_model_in(monkeypatch: pytest.MonkeyPatch, holder: Path | None) -> list[str]:
    """Stand in for the embedding library: the model loads only from `holder`."""
    tried: list[str] = []

    def text_embedding(name: str, cache_dir: str, local_files_only: bool) -> object:
        assert local_files_only  # a download must never be allowed
        tried.append(cache_dir)
        if holder is None or Path(cache_dir) != holder:
            raise ValueError("no such model here")
        return SimpleNamespace(name=name)

    real = importlib.import_module
    library = SimpleNamespace(TextEmbedding=text_embedding)

    def import_module(name: str) -> object:
        return library if name == "fastembed" else real(name)

    monkeypatch.setattr(importlib, "import_module", import_module)
    return tried


def test_the_folder_that_stays_is_looked_in_before_the_one_under_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ARCHIV_EMBED_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    stays = tmp_path / "archiv" / "fastembed"
    assert embed_folders() == [stays, Path("/tmp/fastembed_cache")]
    tried = _library_with_model_in(monkeypatch, stays)
    FastEmbedder()
    assert tried == [str(stays)]


def test_a_model_left_in_the_earlier_folder_is_still_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ARCHIV_EMBED_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    tried = _library_with_model_in(monkeypatch, Path("/tmp/fastembed_cache"))
    FastEmbedder()
    assert tried == [str(tmp_path / "archiv" / "fastembed"), "/tmp/fastembed_cache"]


def test_a_named_folder_is_the_only_one_looked_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ARCHIV_EMBED_CACHE", str(tmp_path / "named"))
    tried = _library_with_model_in(monkeypatch, Path("/tmp/fastembed_cache"))
    with pytest.raises(EmbedderUnavailable, match="never downloads"):
        FastEmbedder()
    assert tried == [str(tmp_path / "named")]


def test_a_missing_model_names_every_folder_looked_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ARCHIV_EMBED_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _library_with_model_in(monkeypatch, None)
    with pytest.raises(EmbedderUnavailable) as caught:
        FastEmbedder()
    assert str(tmp_path / "archiv" / "fastembed") in str(caught.value)
    assert "/tmp/fastembed_cache" in str(caught.value)
