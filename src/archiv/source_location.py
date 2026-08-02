"""Bounded, read-only resolution of immutable Archiv sources."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from archiv.contracts import Citation, NormalizedDocument, SourceLocation
from archiv.hashing import sha256_file
from archiv.search.service import validate_citation
from archiv.storage.layout import ArchivLayout

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_digest(value: str) -> str:
    digest = value.strip()
    if not _SHA256.fullmatch(digest):
        raise ValueError("object identifier must be a lowercase 64-character SHA-256 digest")
    return digest


def _reject_symlink_chain(path: Path, boundary: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise ValueError("canonical source path contains a symbolic link")
        if current == boundary:
            return
        if boundary not in current.parents:
            raise ValueError("canonical source path escapes Archiv-controlled storage")
        current = current.parent


def _resolve_bounded_file(path: Path, boundary: Path, *, label: str) -> Path:
    boundary_resolved = boundary.resolve()
    _reject_symlink_chain(path, boundary)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError(f"{label} is missing") from error
    if not resolved.is_relative_to(boundary_resolved):
        raise ValueError(f"{label} escapes Archiv-controlled storage")
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file")
    return resolved


def _canonical_original(layout: ArchivLayout, digest: str) -> Path:
    original = _resolve_bounded_file(
        layout.original_path(digest),
        layout.originals,
        label="canonical original",
    )
    if sha256_file(original) != digest:
        raise ValueError("canonical original hash mismatch")
    return original


def _normalized_document(layout: ArchivLayout, digest: str) -> NormalizedDocument:
    path = _resolve_bounded_file(
        layout.derived_root(digest) / "normalized" / "document.json",
        layout.derived,
        label="normalized document",
    )
    try:
        document = NormalizedDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as error:
        raise ValueError(f"normalized document is invalid: {error}") from error
    if document.object_sha256 != digest:
        raise ValueError("normalized document object digest mismatch")
    return document


def resolve_object_location(
    object_sha256: str,
    *,
    home: Path | None = None,
) -> SourceLocation:
    """Resolve one canonical object after bounded path and hash validation."""

    digest = _validate_digest(object_sha256)
    layout = ArchivLayout.resolve(home)
    original = _canonical_original(layout, digest)
    document = _normalized_document(layout, digest)
    relative = original.relative_to(layout.root)
    return SourceLocation(
        reference_type="object",
        object_sha256=digest,
        source_name=document.source_name,
        media_type=document.media_type,
        kind=document.kind,
        locator=None,
        canonical_path=str(original),
        canonical_relative_path=relative.as_posix(),
        citation_validated=False,
        original_hash_validated=True,
        read_only=True,
    )


def resolve_citation_location(
    citation: Citation,
    *,
    home: Path | None = None,
) -> SourceLocation:
    """Resolve one citation only after full citation and source revalidation."""

    validation = validate_citation(citation, home=home)
    if not validation.valid:
        raise ValueError("invalid citation: " + "; ".join(validation.errors))
    layout = ArchivLayout.resolve(home)
    original = _canonical_original(layout, citation.object_sha256)
    document = _normalized_document(layout, citation.object_sha256)
    if (
        document.source_name != citation.source_name
        or document.media_type != citation.media_type
        or document.kind != citation.kind
    ):
        raise ValueError("citation source metadata does not match normalized evidence")
    relative = original.relative_to(layout.root)
    return SourceLocation(
        reference_type="citation",
        object_sha256=citation.object_sha256,
        source_name=citation.source_name,
        media_type=citation.media_type,
        kind=citation.kind,
        locator=citation.locator,
        canonical_path=str(original),
        canonical_relative_path=relative.as_posix(),
        citation_validated=True,
        original_hash_validated=True,
        read_only=True,
    )


def _as_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not a JSON object")
    return cast(Mapping[str, object], value)


def _candidate_payloads(payload: object) -> list[object]:
    if isinstance(payload, list):
        return list(cast(list[object], payload))
    mapping = _as_mapping(payload, label="citation file")
    if "retrieved_citations" in mapping:
        value = mapping["retrieved_citations"]
        if not isinstance(value, list):
            raise ValueError("retrieved_citations is not a list")
        return list(cast(list[object], value))
    if "sources" in mapping:
        value = mapping["sources"]
        if not isinstance(value, list):
            raise ValueError("sources is not a list")
        return list(cast(list[object], value))
    return [mapping]


def _citation_from_candidate(candidate: object) -> Citation:
    mapping = _as_mapping(candidate, label="citation candidate")
    nested = mapping.get("citation", mapping)
    try:
        return Citation.model_validate(nested)
    except ValidationError as error:
        raise ValueError(f"citation candidate is invalid: {error}") from error


def load_citation_file(path: Path, *, citation_number: int = 1) -> Citation:
    """Load one explicit citation from common Archiv JSON envelopes."""

    if citation_number < 1:
        raise ValueError("citation number must be at least 1")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"citation file could not be read: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"citation file is not valid JSON: {error}") from error
    candidates = _candidate_payloads(payload)
    if citation_number > len(candidates):
        raise ValueError(
            f"citation number {citation_number} is out of range; file contains {len(candidates)}"
        )
    return _citation_from_candidate(candidates[citation_number - 1])
