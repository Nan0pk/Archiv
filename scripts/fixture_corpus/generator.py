"""Corpus assembly, manifest generation, and command-line entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fixture_corpus.formats import (
    build_docx,
    build_pdf,
    build_png,
    build_pptx,
    build_wav,
    build_xlsx,
)
from fixture_corpus.specs import DEFAULT_OUTPUT, FIXTURES


def _provenance() -> bytes:
    return b"""# Fixture provenance and licensing

All files are generated from synthetic constants by `scripts/generate_fixture_corpus.py`.
No personal, organisational, customer, classified, scraped, or proprietary material is used.

The fixtures have no separate licence and follow the repository's current default terms.
At introduction time Archiv was public but had not selected an open-source licence, so no
reuse rights should be inferred solely from public visibility.

The generator pins its document/image libraries in the development dependency set, fixes
Office metadata, normalizes ZIP ordering/timestamps/permissions, uses invariant PDF output,
and fixes image pixels and WAV samples. LibreOffice headless conversion was used as an
additional compatibility check for the generated DOCX, XLSX, PPTX, and PDF.
"""


def build_files() -> dict[str, bytes]:
    expected = {
        "schema_version": 1,
        "fixtures": FIXTURES,
        "spreadsheet_values": {"workbook.xlsx": {"Evidence!C3": 42.25}},
        "audio_note": (
            "WAV tones test deterministic timing; the marker is stored in "
            "LIST/INFO/ICMT, not spoken."
        ),
    }
    return {
        "operations.txt": (
            b"Archiv representative operations record.\n"
            b"The unique fixture marker connects this file to the research and decision records.\n"
            b"Operational finding: immutable originals remain unchanged throughout the workflow.\n"
        ),
        "research.md": (
            b"# Representative research record\n\n"
            b"The unique fixture marker is repeated here for deterministic cross-file retrieval.\n"
            b"Research finding: citations resolve locally without network access.\n"
        ),
        "decision.txt": (
            b"Archiv representative decision record.\n"
            b"The unique fixture marker identifies the first offline alpha demonstration.\n"
            b"Decision: independent validators determine whether the work succeeded.\n"
        ),
        "plain-text.txt": (
            b"Archiv plain-text fixture\n"
            b"Location test follows\n"
            b"ARCHIV-TEXT-MARKER-2026\n"
            b"End of fixture\n"
        ),
        "report.pdf": build_pdf(),
        "document.docx": build_docx(),
        "workbook.xlsx": build_xlsx(),
        "presentation.pptx": build_pptx(),
        "scanned-page.png": build_png(),
        "sample.wav": build_wav(),
        "malformed/truncated.pdf": (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"),
        "malformed/not-a-docx.docx": (
            b"ARCHIV malformed DOCX fixture: intentionally not a ZIP package.\n"
        ),
        "malformed/corrupt.wav": b"RIFF\x08\0\0\0NOTWAVE!",
        "expected.json": (json.dumps(expected, indent=2, sort_keys=True) + "\n").encode(),
        "PROVENANCE.md": _provenance(),
    }


def generate(output: Path) -> None:
    files = build_files()
    output.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    entries = [
        {
            "path": path,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "media_type": FIXTURES.get(path, {}).get(
                "media_type",
                "application/json" if path.endswith(".json") else "text/markdown",
            ),
            "marker": FIXTURES.get(path, {}).get("marker"),
            "license": "repository-default; no separate fixture licence",
        }
        for path, content in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "generator_version": 1,
        "generator": "scripts/generate_fixture_corpus.py",
        "entries": entries,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    generate(parser.parse_args().output)
