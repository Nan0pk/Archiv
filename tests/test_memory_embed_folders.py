"""Where the embedding model files are looked for."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from archiv.memory import vectors
from archiv.memory.vectors import EmbedderUnavailable, FastEmbedder, embed_folders


def _filled(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "a-model-file").write_text("x", encoding="utf-8")
    return folder


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


def _defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point both default folders at places inside `tmp_path`, so /tmp on this machine is unused."""
    monkeypatch.delenv("ARCHIV_EMBED_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    earlier = tmp_path / "earlier"
    monkeypatch.setattr(vectors, "_EARLIER_FOLDER", earlier)
    stays = tmp_path / "cache" / "archiv" / "fastembed"
    assert embed_folders() == [stays, earlier]
    return stays, earlier


def test_the_folder_that_stays_is_looked_in_before_the_earlier_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stays, earlier = _defaults(monkeypatch, tmp_path)
    _filled(earlier)
    tried = _library_with_model_in(monkeypatch, _filled(stays))
    FastEmbedder()
    assert tried == [str(stays)]


def test_a_model_left_in_the_earlier_folder_is_still_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stays, earlier = _defaults(monkeypatch, tmp_path)
    tried = _library_with_model_in(monkeypatch, _filled(earlier))
    FastEmbedder()
    assert tried == [str(earlier)]  # the folder that stays does not exist, so it is not tried
    assert not stays.exists()


def test_a_named_folder_is_the_only_one_looked_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stays, earlier = _defaults(monkeypatch, tmp_path)
    named = _filled(tmp_path / "named")
    monkeypatch.setenv("ARCHIV_EMBED_CACHE", str(named))
    tried = _library_with_model_in(monkeypatch, _filled(earlier))
    with pytest.raises(EmbedderUnavailable, match="never downloads"):
        FastEmbedder()
    assert tried == [str(named)]


def test_a_missing_model_names_every_folder_and_creates_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stays, earlier = _defaults(monkeypatch, tmp_path)
    tried = _library_with_model_in(monkeypatch, None)
    with pytest.raises(EmbedderUnavailable) as caught:
        FastEmbedder()
    assert str(stays) in str(caught.value)
    assert str(earlier) in str(caught.value)
    assert tried == []  # the library is not asked about a folder that is not there
    assert not stays.exists()
    assert not earlier.exists()
