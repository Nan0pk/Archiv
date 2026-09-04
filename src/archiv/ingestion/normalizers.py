"""Bounded deterministic format validation and normalization."""

from __future__ import annotations

from pathlib import Path

from archiv.contracts import NormalizedDocument
from archiv.ingestion.extractors import (
    ALL_EXTRACTORS,
    Extractor,
    check_content_signature,
    get_extractor,
)
from archiv.ingestion.formats import (
    MalformedInputError,
    UnsupportedFormatError,
    media_type_for,
    suffix_for,
)
from archiv.ingestion.limits import NativeResourceLimitError, check_input


def normalize(
    path: Path,
    digest: str,
    *,
    source_name: str | None = None,
) -> NormalizedDocument:
    """Validate one file and convert it into Archiv's portable document schema."""

    logical_name = source_name or path.name
    suffix = suffix_for(logical_name)
    kind = suffix.lstrip(".")
    media_type = media_type_for(logical_name)
    try:
        check_input(path)
        extractor = get_extractor(suffix)
        check_content_signature(path, extractor, suffix)
        return extractor.normalize(
            path,
            digest,
            source_name=logical_name,
            media_type=media_type,
            kind=kind,
        )
    except (NativeResourceLimitError, UnsupportedFormatError, MalformedInputError):
        raise
    except Exception as error:
        raise MalformedInputError(f"{type(error).__name__}: {error}") from error


__all__ = [
    "ALL_EXTRACTORS",
    "Extractor",
    "MalformedInputError",
    "UnsupportedFormatError",
    "get_extractor",
    "media_type_for",
    "normalize",
    "suffix_for",
]
