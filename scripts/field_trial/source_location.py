"""Public-safe bounded source-location probe for field-trial summaries."""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from field_trial.scoring import _markdown, scan_safe_artifacts

_MARKER = "ARCHIV-SOURCE-LOCATION-PROBE-2026"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown command failure"
        raise RuntimeError(f"source-location probe failed: {detail}")
    return result


def apply_source_location_probe(
    summary: dict[str, object],
    *,
    output: Path,
    archiv_command: str,
) -> dict[str, object]:
    """Prove bounded source location without retaining local paths or source metadata."""

    executable = shlex.split(archiv_command)
    if not executable:
        raise ValueError("archiv command must not be empty")

    with tempfile.TemporaryDirectory(prefix="archiv-source-location-probe-") as temporary:
        root = Path(temporary)
        home = root / "home"
        source = root / "public-source-location-probe.txt"
        citation_file = root / "citation.json"
        source.write_text(_MARKER + "\n", encoding="utf-8")

        _run([*executable, "add", str(source), "--home", str(home), "--json"])
        find = _run([*executable, "find", _MARKER, "--home", str(home), "--json"])
        citations = json.loads(find.stdout)
        citation_file.write_text(json.dumps(citations), encoding="utf-8")
        located = _run(
            [
                *executable,
                "source",
                "--citation-file",
                str(citation_file),
                "--home",
                str(home),
                "--json",
            ]
        )
        payload = cast(Mapping[str, object], json.loads(located.stdout))

    passed = (
        payload.get("reference_type") == "citation"
        and payload.get("citation_validated") is True
        and payload.get("original_hash_validated") is True
        and payload.get("read_only") is True
    )
    if not passed:
        raise RuntimeError("source-location probe returned an unvalidated result")

    navigation = cast(dict[str, object], summary.setdefault("source_navigation", {}))
    navigation["bounded_source_location_available"] = True
    navigation["citation_revalidated"] = True
    navigation["original_hash_revalidated"] = True
    navigation["read_only"] = True

    defects = cast(list[dict[str, object]], summary.get("defects", []))
    summary["defects"] = [
        defect for defect in defects if defect.get("category") != "source navigation friction"
    ]

    output.mkdir(parents=True, exist_ok=True)
    (output / "public-results.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "public-report.md").write_text(_markdown(summary), encoding="utf-8")
    errors = scan_safe_artifacts(output)
    if errors:
        raise RuntimeError("source-location artifact safety check failed: " + "; ".join(errors))
    return summary
