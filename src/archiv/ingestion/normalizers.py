"""Bounded deterministic format validation and normalization."""

from __future__ import annotations

from pathlib import Path

from archiv.contracts import NormalizedDocument
from archiv.ingestion.formats import (
    MalformedInputError,
    UnsupportedFormatError,
    media_type_for,
    suffix_for,
)
from archiv.ingestion.limits import check_input
from archiv.ingestion.normalize_documents import normalize_docx, normalize_pdf, normalize_text
from archiv.ingestion.normalize_inpage import normalize_inpage
from archiv.ingestion.normalize_media import normalize_image, normalize_wav
from archiv.ingestion.normalize_odb import normalize_odb
from archiv.ingestion.normalize_odf import ODF_MIMETYPES, normalize_odf
from archiv.ingestion.normalize_office import normalize_pptx, normalize_xlsx


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
        if kind in {"txt", "md"}:
            return normalize_text(
                path,
                digest,
                source_name=logical_name,
                kind=kind,
                media_type=media_type,
            )
        if kind == "pdf":
            return normalize_pdf(path, digest, source_name=logical_name, media_type=media_type)
        if kind == "docx":
            return normalize_docx(path, digest, source_name=logical_name, media_type=media_type)
        if kind == "xlsx":
            return normalize_xlsx(path, digest, source_name=logical_name, media_type=media_type)
        if kind == "pptx":
            return normalize_pptx(path, digest, source_name=logical_name, media_type=media_type)
        if kind == "inp":
            return normalize_inpage(
                path,
                digest,
                source_name=logical_name,
                media_type=media_type,
            )
        if kind == "odb":
            return normalize_odb(
                path,
                digest,
                source_name=logical_name,
                media_type=media_type,
            )
        if kind in ODF_MIMETYPES:
            return normalize_odf(
                path,
                digest,
                source_name=logical_name,
                media_type=media_type,
                kind=kind,
            )
        if kind in {"png", "jpg", "jpeg"}:
            return normalize_image(
                path,
                digest,
                source_name=logical_name,
                media_type=media_type,
                kind=kind,
            )
        if kind == "wav":
            return normalize_wav(path, digest, source_name=logical_name, media_type=media_type)
    except Exception as error:
        raise MalformedInputError(f"{type(error).__name__}: {error}") from error

    raise UnsupportedFormatError(f"unsupported file type: {suffix}")


__all__ = [
    "MalformedInputError",
    "UnsupportedFormatError",
    "media_type_for",
    "normalize",
    "suffix_for",
]
