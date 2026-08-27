"""Privacy-preserving aggregate ingestion telemetry.

Summaries are deliberately constructed from counters only.  They are suitable
for a user-selected export, but are never transmitted by Archiv.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from archiv.contracts import StrictModel


class IngestionCounts(StrictModel):
    """Aggregate observations; degraded and skipped may overlap supported files."""

    supported: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    degraded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class IngestionSummary(StrictModel):
    """Export contract containing no names, paths, hashes, excerpts, or content."""

    schema_version: str = "1"
    privacy: str = "aggregate_counts_only"
    local_only: bool = True
    counts: IngestionCounts


def write_summary(path: Path, counts: IngestionCounts) -> Path:
    """Atomically write a private-by-default, aggregate-only JSON export."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    payload = IngestionSummary(counts=counts).model_dump_json(indent=2) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def validate_summary(path: Path) -> IngestionSummary:
    """Validate an export strictly, including the absence of extra fields."""

    return IngestionSummary.model_validate(json.loads(path.read_text(encoding="utf-8")))
