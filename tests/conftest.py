from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from ingestion_support import GENERATOR


@pytest.fixture(autouse=True)
def disable_optional_ocr(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep unrelated tests independent of host-installed OCR tools."""

    monkeypatch.setenv("ARCHIV_OCR", "off")
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
