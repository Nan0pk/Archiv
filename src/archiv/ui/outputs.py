"""Inspect a finished console run for verified, openable results.

The console opens only two kinds of targets through the operating system's
default handler:

* artifacts that a command actually produced — detected from structured JSON
  output or from output paths the user typed into the form, each revalidated
  against Archiv-controlled storage or the user's own explicit entries; and
* preserved sources behind citations — always resolved through Archiv's
  existing bounded source-location validators before any file is opened.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from archiv.contracts import Citation
from archiv.source_location import load_citation_file, resolve_citation_location
from archiv.storage.layout import ArchivLayout
from archiv.ui.errors import UiError

_PATH_KEYS = {
    "path",
    "output",
    "backup",
    "export",
    "sample_vault",
    "archive",
    "archive_path",
    "canonical_path",
    "docx_path",
    "pdf_path",
    "manifest_path",
    "validation_path",
    "evidence_dir",
    "report_dir",
}


@dataclass(frozen=True)
class OpenableArtifact:
    """One verified file or directory the console may open."""

    path: Path
    origin: str
    detail: str


@dataclass
class RunOutputs:
    """What one finished run left behind for the operator."""

    run_json: object | None
    citation_count: int = 0
    artifacts: list[OpenableArtifact] = field(default_factory=lambda: list[OpenableArtifact]())
    model_provenance: str | None = None
    """Where the answer came from, when the run said. `None` when it did not say.

    Carried out of the run's own JSON rather than assumed, so the desktop console can
    tell the operator that an answer was not produced on this machine. Without it the
    console shows a remote answer exactly like a local one.
    """


def parse_run_json(output: str) -> object | None:
    """Return the command's structured output when the run emitted pure JSON."""

    text = output.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_strings(payload: object) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    stack: list[object] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            items = cast(dict[object, object], current).items()
            for key, value in items:
                if isinstance(value, str) and isinstance(key, str):
                    found.append((key, value))
                else:
                    stack.append(value)
        elif isinstance(current, list | tuple):
            stack.extend(cast(list[object] | tuple[object, ...], current))
    return found


def _citation_candidates(payload: object) -> list[object]:
    """Select citation candidates exactly like the official envelopes parser."""

    if isinstance(payload, list):
        return list(cast(list[object], payload))
    if isinstance(payload, Mapping):
        mapping = cast(Mapping[str, object], payload)
        for key in ("retrieved_citations", "sources"):
            value = mapping.get(key)
            if isinstance(value, list):
                return list(cast(list[object], value))
        return [payload]
    return []


def _model_provenance(payload: object) -> str | None:
    """Read `model.provenance` out of a run's JSON, if the run recorded one."""

    if not isinstance(payload, dict):
        return None
    model = cast(dict[str, object], payload).get("model")
    if not isinstance(model, dict):
        return None
    provenance = cast(dict[str, object], model).get("provenance")
    return provenance if isinstance(provenance, str) else None


def _citation_candidate_count(payload: object) -> int:
    """Count resolvable citations for listing purposes only.

    Opening never trusts this count; every open goes through the bounded
    source-location validator.  The count only drives console labelling.
    """

    count = 0
    for candidate in _citation_candidates(payload):
        nested: object = candidate
        if isinstance(candidate, Mapping):
            nested = cast(Mapping[str, object], candidate).get("citation", cast(object, candidate))
        try:
            Citation.model_validate(nested)
        except ValueError:
            continue
        count += 1
    return count


def resolve_citation_path(payload: object, *, citation_number: int, home: Path | None) -> Path:
    """Resolve one citation to its preserved source through Archiv's validator.

    The run's JSON payload is replayed through
    :func:`archiv.source_location.load_citation_file` and
    :func:`archiv.source_location.resolve_citation_location`, so console
    source opening has exactly the trust properties of ``archiv source``.
    """

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", prefix="archiv-console-"
    ) as handle:
        json.dump(payload, handle)
        handle.flush()
        citation = load_citation_file(Path(handle.name), citation_number=citation_number)
    location = resolve_citation_location(citation, home=home)
    return Path(location.canonical_path)


def _contains_symlink(path: Path, boundary: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == boundary:
            return False
        if current.parent == current:
            return False
        if boundary not in current.parents:
            return False
        current = current.parent


def verify_artifact(
    candidate: Path,
    *,
    home: Path | None,
    user_paths: tuple[Path, ...] = (),
) -> Path:
    """Accept an artifact only inside Archiv storage or the user's entries."""

    resolved_home: Path | None = None
    try:
        resolved_home = ArchivLayout.resolve(home).root
    except (OSError, RuntimeError, ValueError):
        resolved_home = None
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise UiError(f"artifact does not exist: {candidate}") from error
    allowed = False
    if resolved_home is not None and resolved.is_relative_to(resolved_home):
        if _contains_symlink(resolved, resolved_home):
            raise UiError("artifact lies behind a symbolic link inside Archiv storage")
        allowed = True
    if not allowed:
        for user_path in user_paths:
            try:
                resolved_user = user_path.expanduser().resolve()
            except OSError:
                continue
            if resolved == resolved_user:
                allowed = True
                break
    if not allowed:
        raise UiError(
            "artifact is neither inside Archiv-controlled storage nor an explicit "
            "output path entered in the console"
        )
    if not resolved.is_file() and not resolved.is_dir():
        raise UiError("artifact is not a regular file or directory")
    return resolved


def inspect_run_output(
    output: str,
    *,
    home: Path | None = None,
    user_paths: tuple[Path, ...] = (),
) -> RunOutputs:
    """Extract verified artifacts and citation counts from a finished run."""

    payload = parse_run_json(output)
    result = RunOutputs(run_json=payload)
    if payload is None:
        return result
    result.citation_count = _citation_candidate_count(payload)
    result.model_provenance = _model_provenance(payload)

    seen: set[Path] = set()
    for key, value in _walk_strings(payload):
        if not (key in _PATH_KEYS or key.endswith("_path")):
            continue
        if not value.startswith("/"):
            continue
        candidate = Path(value)
        if candidate in seen:
            continue
        try:
            verified = verify_artifact(candidate, home=home, user_paths=user_paths)
        except UiError:
            continue
        seen.add(verified)
        result.artifacts.append(OpenableArtifact(path=verified, origin=f"json:{key}", detail=value))
    return result


def collect_user_paths(values: Mapping[str, object]) -> tuple[Path, ...]:
    """Collect paths the operator explicitly typed into the console form."""

    collected: list[Path] = []
    for value in values.values():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text.startswith("/") or text.startswith("~"):
            collected.append(Path(text))
    return tuple(collected)
