#!/usr/bin/env python3
"""Build the rendered Office artifact bundle used by GitHub Actions."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from archiv.ingestion import ingest_file
from archiv.report_contracts import ReportStatus
from archiv.reports import generate_report
from archiv.search import rebuild_search_index

FIXTURES = [
    "plain-text.txt",
    "report.pdf",
    "document.docx",
    "workbook.xlsx",
    "presentation.pptx",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus = output_dir / "corpus"
    home = output_dir / "archiv-home"
    generator = Path(__file__).with_name("generate_fixture_corpus.py")
    subprocess.run(
        [sys.executable, str(generator), "--output", str(corpus)],
        check=True,
    )
    for filename in FIXTURES:
        ingest_file(corpus / filename, home=home)
    rebuild_search_index(home=home)
    result = generate_report(
        "MARKER",
        output_dir / "archiv-evidence-report.docx",
        home=home,
        max_sources=len(FIXTURES),
        render=True,
        evidence_dir=output_dir,
    )
    if result.status is not ReportStatus.SUCCEEDED:
        raise SystemExit(
            "rendered report validation failed: " + "; ".join(result.validation.errors)
        )


if __name__ == "__main__":
    main()
