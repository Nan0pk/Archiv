"""Stable fixture specifications and deterministic timestamps."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

FIXED_TIME: Final = (1980, 1, 1, 0, 0, 0)
FIXED_DATETIME: Final = datetime(2026, 1, 1, tzinfo=UTC)
DEFAULT_OUTPUT: Final = Path("build/fixtures/representative-corpus")

FIXTURES: Final[dict[str, dict[str, object]]] = {
    "operations.txt": {
        "media_type": "text/plain",
        "marker": "unique fixture marker",
        "location": {"line": 2},
        "expected_valid": True,
    },
    "research.md": {
        "media_type": "text/markdown",
        "marker": "unique fixture marker",
        "location": {"line": 3},
        "expected_valid": True,
    },
    "decision.txt": {
        "media_type": "text/plain",
        "marker": "unique fixture marker",
        "location": {"line": 2},
        "expected_valid": True,
    },
    "plain-text.txt": {
        "media_type": "text/plain",
        "marker": "ARCHIV-TEXT-MARKER-2026",
        "location": {"line": 3},
        "expected_valid": True,
    },
    "report.pdf": {
        "media_type": "application/pdf",
        "marker": "ARCHIV-PDF-MARKER-2026",
        "location": {"page": 1},
        "expected_valid": True,
    },
    "document.docx": {
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "marker": "ARCHIV-DOCX-MARKER-2026",
        "location": {"paragraph": 2},
        "expected_valid": True,
    },
    "workbook.xlsx": {
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "marker": "ARCHIV-XLSX-MARKER-2026",
        "location": {"sheet": "Evidence", "cell": "B2"},
        "expected_valid": True,
    },
    "presentation.pptx": {
        "media_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "marker": "ARCHIV-PPTX-MARKER-2026",
        "location": {"slide": 1, "shape": 2},
        "expected_valid": True,
    },
    "scanned-page.png": {
        "media_type": "image/png",
        "marker": "ARCHIV-IMAGE-MARKER-2026",
        "location": {"page": 1, "bounding_box": [20, 20, 246, 31]},
        "expected_valid": True,
    },
    "sample.wav": {
        "media_type": "audio/wav",
        "marker": "ARCHIV-AUDIO-MARKER-2026",
        "location": {
            "metadata_chunk": "LIST/INFO/ICMT",
            "tone_segments_ms": [[0, 250], [300, 550], [600, 850]],
        },
        "expected_valid": True,
    },
    "malformed/truncated.pdf": {
        "media_type": "application/pdf",
        "marker": None,
        "location": {"expected": "reject"},
        "expected_valid": False,
    },
    "malformed/not-a-docx.docx": {
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "marker": None,
        "location": {"expected": "reject"},
        "expected_valid": False,
    },
    "malformed/corrupt.wav": {
        "media_type": "audio/wav",
        "marker": None,
        "location": {"expected": "reject"},
        "expected_valid": False,
    },
}
