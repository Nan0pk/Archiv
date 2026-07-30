from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from ingestion_support import GENERATOR


@pytest.fixture
def ingestion_corpus(tmp_path: Path) -> Path:
    """Generate the public-safe corpus used by ingestion acceptance tests."""

    output = tmp_path / "corpus"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output)],
        check=True,
    )
    return output
