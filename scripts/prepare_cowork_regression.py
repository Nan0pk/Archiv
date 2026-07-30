#!/usr/bin/env python3
"""Prepare deterministic Archiv state for a CoWork transport regression."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from archiv.executor.source_marker import run_source_marker
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index

FIXTURES = ("plain-text.txt", "document.docx", "report.pdf")
MARKER = "COWORK-ARCHIV-EXACT-2026"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("pinned", "current"), required=True)
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

    source_hashes: dict[str, str] = {}
    for filename in FIXTURES:
        source = corpus / filename
        source_hashes[str(source)] = sha256_file(source)
        ingest_file(source, home=home)
    rebuild_search_index(home=home)

    exact_workspace = output_dir / "exact-source-marker"
    exact_workspace.mkdir()
    exact_source = exact_workspace / "source.txt"
    exact_source.write_text(MARKER + "\n", encoding="utf-8")
    exact_source_hash = sha256_file(exact_source)
    exact_result = run_source_marker(exact_workspace)
    exact_output = exact_workspace / "outputs" / "probe.txt"
    expected_output = f"HARNESS_OK\n{MARKER}\n".encode()
    exact_succeeded = (
        exact_result.status == "succeeded"
        and exact_output.is_file()
        and exact_output.read_bytes() == expected_output
        and sha256_file(exact_source) == exact_source_hash
    )
    if not exact_succeeded:
        raise SystemExit("exact source-marker regression failed before CoWork integration")

    plan: dict[str, object] = {
        "schema_version": 1,
        "mode": arguments.mode,
        "archiv_home": str(home),
        "corpus": str(corpus),
        "source_hashes": source_hashes,
        "exact_task": {
            "workspace": str(exact_workspace),
            "source_hash": exact_source_hash,
            "status": exact_result.status,
            "run_id": exact_result.run_id,
            "evidence_dir": exact_result.evidence_dir,
            "output_path": str(exact_output),
            "expected_output": expected_output.decode("utf-8"),
        },
    }
    output_dir.joinpath("regression-plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
