#!/usr/bin/env python3
"""Build synthetic MCP evidence artifacts for GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from archiv.contracts import Citation
from archiv.hashing import sha256_file
from archiv.mcp_tools import (
    archiv_generate_docx,
    archiv_get_run_evidence,
    archiv_ingest,
    archiv_read_source,
    archiv_search,
    archiv_verify_artifact,
)
from archiv.search import rebuild_search_index

FIXTURES = ["plain-text.txt", "document.docx", "report.pdf"]


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
    os.environ["ARCHIV_HOME"] = str(home)
    source_hashes = {filename: sha256_file(corpus / filename) for filename in FIXTURES}

    calls = [archiv_ingest(str(corpus / filename)) for filename in FIXTURES]
    rebuild_search_index(home=home)
    searched = archiv_search("MARKER", limit=10)
    calls.append(searched)
    results = cast(list[dict[str, object]], searched.result["results"])
    citation = Citation.model_validate(results[0]["citation"])
    calls.append(archiv_read_source(citation))
    calls.append(archiv_generate_docx("MARKER", "mcp-validation.docx", max_sources=3))
    calls.append(archiv_verify_artifact("mcp-validation.docx"))
    calls.append(archiv_get_run_evidence(searched.run_id))

    if any(call.status != "succeeded" for call in calls):
        raise SystemExit("one or more MCP validation calls did not succeed")
    if {filename: sha256_file(corpus / filename) for filename in FIXTURES} != source_hashes:
        raise SystemExit("synthetic source hash changed during MCP validation")

    summary: dict[str, object] = {
        "schema_version": "1",
        "network_policy": "denied",
        "tool_runs": [
            {
                "run_id": call.run_id,
                "tool": call.tool,
                "status": call.status,
                "evidence_dir": call.evidence_dir,
            }
            for call in calls
        ],
        "source_hashes": source_hashes,
    }
    output_dir.joinpath("summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
