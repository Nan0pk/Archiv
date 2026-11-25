from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from ingestion_support import GENERATOR


@pytest.fixture(autouse=True)
def disable_optional_ocr(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """Keep unrelated tests independent of host-installed OCR tools."""

    monkeypatch.setenv("ARCHIV_OCR", "off")
    # A library remembered by `archiv setup` on this machine must not redirect tests.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))
    yield


@pytest.fixture
def ingestion_corpus(tmp_path: Path) -> Path:
    """Generate the public-safe corpus used by ingestion acceptance tests."""

    output = tmp_path / "corpus"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output)],
        check=True,
    )
    return output
