"""Supported ingestion formats and media-type resolution."""

from __future__ import annotations

import mimetypes
from pathlib import Path


class UnsupportedFormatError(ValueError):
    """The input format is outside Archiv's current ingestion surface."""


class MalformedInputError(ValueError):
    """The claimed input format could not be parsed safely."""


SUPPORTED_SUFFIXES = {
    ".txt",
    ".md",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".odt",
    ".ott",
    ".odm",
    ".otm",
    ".ods",
    ".ots",
    ".odp",
    ".otp",
    ".odg",
    ".otg",
    ".odf",
    ".odb",
    ".fodt",
    ".fods",
    ".fodp",
    ".fodg",
    ".png",
    ".jpg",
    ".jpeg",
    ".wav",
}
MEDIA_TYPES = {
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ott": "application/vnd.oasis.opendocument.text-template",
    ".odm": "application/vnd.oasis.opendocument.text-master",
    ".otm": "application/vnd.oasis.opendocument.text-master-template",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".otp": "application/vnd.oasis.opendocument.presentation-template",
    ".odg": "application/vnd.oasis.opendocument.graphics",
    ".otg": "application/vnd.oasis.opendocument.graphics-template",
    ".odf": "application/vnd.oasis.opendocument.formula",
    ".odb": "application/vnd.oasis.opendocument.base",
    ".fodt": "text/xml",
    ".fods": "text/xml",
    ".fodp": "text/xml",
    ".fodg": "text/xml",
    ".wav": "audio/wav",
}


def suffix_for(source_name: str) -> str:
    """Return a validated lowercase suffix for a logical source name."""

    suffix = Path(source_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFormatError(f"unsupported file type: {suffix or '<none>'}")
    return suffix


def media_type_for(source_name: str) -> str:
    """Return an explicit or standard-library media type for a source name."""

    suffix = suffix_for(source_name)
    result = MEDIA_TYPES.get(suffix) or mimetypes.guess_type(source_name)[0]
    if result is None:
        raise UnsupportedFormatError(f"media type could not be detected: {source_name}")
    return result
