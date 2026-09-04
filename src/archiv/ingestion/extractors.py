"""Extractor registry and signature-first format detection.

Replaces the hardcoded normalize() dispatch ladder with declared,
reusable extractors and signature-first format detection (issue #37, PR 108).
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from archiv.contracts import NormalizedDocument
from archiv.ingestion.formats import MalformedInputError, UnsupportedFormatError
from archiv.ingestion.normalize_archive import normalize_archive
from archiv.ingestion.normalize_documents import normalize_docx, normalize_pdf, normalize_text
from archiv.ingestion.normalize_inpage import normalize_inpage
from archiv.ingestion.normalize_legacy_office import normalize_doc, normalize_ppt, normalize_xls
from archiv.ingestion.normalize_media import normalize_image, normalize_wav
from archiv.ingestion.normalize_odb import normalize_odb
from archiv.ingestion.normalize_odf import ODF_MIMETYPES, normalize_odf
from archiv.ingestion.normalize_office import normalize_pptx, normalize_xlsx
from archiv.ingestion.normalize_rtf import normalize_rtf
from archiv.ingestion.normalize_svg import normalize_svg

# Known binary signatures that must never appear in plain-text documents
KNOWN_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"%PDF-",
    b"PK\x03\x04",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"{\\rtf",
    b"RIFF",
    b"SQLite format 3\x00",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"II*\x00",
    b"MM\x00*",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
)


class NormalizeFunc(Protocol):
    def __call__(
        self,
        path: Path,
        digest: str,
        *,
        source_name: str,
        media_type: str,
        kind: str,
    ) -> NormalizedDocument: ...


@dataclass(frozen=True)
class Extractor:
    name: str
    version: str  # bumped => derived data is stale => rebuild
    suffixes: frozenset[str]
    media_types: frozenset[str]
    magic: tuple[bytes, ...]  # content signatures, checked before suffix
    kind: str  # normalized document kind
    normalize: NormalizeFunc
    cost: Literal["fast", "deep"] = "fast"


def _norm_text(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_text(path, digest, source_name=source_name, kind=kind, media_type=media_type)


def _norm_pdf(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_pdf(path, digest, source_name=source_name, media_type=media_type)


def _norm_doc(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_doc(path, digest, source_name=source_name, media_type=media_type)


def _norm_rtf(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_rtf(path, digest, source_name=source_name, media_type=media_type)


def _norm_docx(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_docx(path, digest, source_name=source_name, media_type=media_type)


def _norm_xls(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_xls(path, digest, source_name=source_name, media_type=media_type)


def _norm_xlsx(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_xlsx(path, digest, source_name=source_name, media_type=media_type)


def _norm_ppt(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_ppt(path, digest, source_name=source_name, media_type=media_type)


def _norm_pptx(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_pptx(path, digest, source_name=source_name, media_type=media_type)


def _norm_inpage(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_inpage(path, digest, source_name=source_name, media_type=media_type)


def _norm_odb(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_odb(path, digest, source_name=source_name, media_type=media_type)


def _norm_odf(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_odf(path, digest, source_name=source_name, media_type=media_type, kind=kind)


def _norm_image(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_image(path, digest, source_name=source_name, media_type=media_type, kind=kind)


def _norm_wav(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_wav(path, digest, source_name=source_name, media_type=media_type)


def _norm_svg(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_svg(path, digest, source_name=source_name, media_type=media_type)


def _norm_archive(
    path: Path, digest: str, *, source_name: str, media_type: str, kind: str
) -> NormalizedDocument:
    return normalize_archive(
        path, digest, source_name=source_name, media_type=media_type, kind=kind
    )


ALL_EXTRACTORS: tuple[Extractor, ...] = (
    Extractor(
        name="plain-text",
        version="1",
        suffixes=frozenset({".txt", ".md"}),
        media_types=frozenset({"text/plain", "text/markdown"}),
        magic=(),
        kind="text",
        normalize=_norm_text,
        cost="fast",
    ),
    Extractor(
        name="pdf",
        version="1",
        suffixes=frozenset({".pdf"}),
        media_types=frozenset({"application/pdf"}),
        magic=(b"%PDF-",),
        kind="pdf",
        normalize=_norm_pdf,
        cost="fast",
    ),
    Extractor(
        name="legacy-word",
        version="1",
        suffixes=frozenset({".doc"}),
        media_types=frozenset({"application/msword"}),
        magic=(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        kind="doc",
        normalize=_norm_doc,
        cost="fast",
    ),
    Extractor(
        name="legacy-rtf",
        version="1",
        suffixes=frozenset({".rtf"}),
        media_types=frozenset({"application/rtf"}),
        magic=(b"{\\rtf",),
        kind="rtf",
        normalize=_norm_rtf,
        cost="fast",
    ),
    Extractor(
        name="docx",
        version="1",
        suffixes=frozenset({".docx"}),
        media_types=frozenset(
            {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ),
        magic=(b"PK\x03\x04",),
        kind="docx",
        normalize=_norm_docx,
        cost="fast",
    ),
    Extractor(
        name="legacy-excel",
        version="1",
        suffixes=frozenset({".xls"}),
        media_types=frozenset({"application/vnd.ms-excel"}),
        magic=(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        kind="xls",
        normalize=_norm_xls,
        cost="fast",
    ),
    Extractor(
        name="xlsx",
        version="1",
        suffixes=frozenset({".xlsx"}),
        media_types=frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
        magic=(b"PK\x03\x04",),
        kind="xlsx",
        normalize=_norm_xlsx,
        cost="fast",
    ),
    Extractor(
        name="legacy-powerpoint",
        version="1",
        suffixes=frozenset({".ppt"}),
        media_types=frozenset({"application/vnd.ms-powerpoint"}),
        magic=(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        kind="ppt",
        normalize=_norm_ppt,
        cost="fast",
    ),
    Extractor(
        name="pptx",
        version="1",
        suffixes=frozenset({".pptx"}),
        media_types=frozenset(
            {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
        ),
        magic=(b"PK\x03\x04",),
        kind="pptx",
        normalize=_norm_pptx,
        cost="fast",
    ),
    Extractor(
        name="inpage",
        version="1",
        suffixes=frozenset({".inp"}),
        media_types=frozenset({"application/x-inpage"}),
        magic=(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
        kind="inp",
        normalize=_norm_inpage,
        cost="fast",
    ),
    Extractor(
        name="odb",
        version="1",
        suffixes=frozenset({".odb"}),
        media_types=frozenset({"application/vnd.oasis.opendocument.base"}),
        magic=(b"PK\x03\x04",),
        kind="odb",
        normalize=_norm_odb,
        cost="fast",
    ),
    Extractor(
        name="odf-package",
        version="1",
        suffixes=frozenset({f".{k}" for k in ODF_MIMETYPES if not k.startswith("f")}),
        media_types=frozenset({v for k, v in ODF_MIMETYPES.items() if not k.startswith("f")}),
        magic=(b"PK\x03\x04",),
        kind="odf",
        normalize=_norm_odf,
        cost="fast",
    ),
    Extractor(
        name="odf-flat",
        version="1",
        suffixes=frozenset({".fodt", ".fods", ".fodp", ".fodg"}),
        media_types=frozenset({"text/xml"}),
        magic=(b"<?xml", b"<office:document", b"<office:document-content", b"<"),
        kind="odf",
        normalize=_norm_odf,
        cost="fast",
    ),
    Extractor(
        name="png-image",
        version="1",
        suffixes=frozenset({".png"}),
        media_types=frozenset({"image/png"}),
        magic=(b"\x89PNG\r\n\x1a\n",),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="jpeg-image",
        version="1",
        suffixes=frozenset({".jpg", ".jpeg"}),
        media_types=frozenset({"image/jpeg"}),
        magic=(b"\xff\xd8\xff",),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="gif-image",
        version="1",
        suffixes=frozenset({".gif"}),
        media_types=frozenset({"image/gif"}),
        magic=(b"GIF87a", b"GIF89a"),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="bmp-image",
        version="1",
        suffixes=frozenset({".bmp"}),
        media_types=frozenset({"image/bmp"}),
        magic=(b"BM",),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="tiff-image",
        version="1",
        suffixes=frozenset({".tiff", ".tif"}),
        media_types=frozenset({"image/tiff"}),
        magic=(b"II*\x00", b"MM\x00*"),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="webp-image",
        version="1",
        suffixes=frozenset({".webp"}),
        media_types=frozenset({"image/webp"}),
        magic=(b"RIFF",),
        kind="image",
        normalize=_norm_image,
        cost="fast",
    ),
    Extractor(
        name="wav",
        version="1",
        suffixes=frozenset({".wav"}),
        media_types=frozenset({"audio/wav"}),
        magic=(b"RIFF",),
        kind="wav",
        normalize=_norm_wav,
        cost="fast",
    ),
    Extractor(
        name="svg",
        version="1",
        suffixes=frozenset({".svg"}),
        media_types=frozenset({"image/svg+xml"}),
        magic=(b"<?xml", b"<svg", b"<!DOCTYPE svg", b"<!doctype svg", b"<"),
        kind="svg",
        normalize=_norm_svg,
        cost="fast",
    ),
    Extractor(
        name="archive-zip",
        version="1",
        suffixes=frozenset({".zip"}),
        media_types=frozenset({"application/zip"}),
        magic=(b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
        kind="archive",
        normalize=_norm_archive,
        cost="fast",
    ),
    Extractor(
        name="archive-tar",
        version="1",
        suffixes=frozenset({".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}),
        media_types=frozenset(
            {
                "application/x-tar",
                "application/gzip",
                "application/x-bzip2",
                "application/x-xz",
            }
        ),
        magic=(
            b"\x1f\x8b",
            b"BZh",
            b"\xfd7zXZ\x00",
            b"ustar",
        ),
        kind="archive",
        normalize=_norm_archive,
        cost="fast",
    ),
)

_EXTRACTOR_BY_SUFFIX: dict[str, Extractor] = {
    suffix: extractor for extractor in ALL_EXTRACTORS for suffix in extractor.suffixes
}


def get_extractor(suffix: str) -> Extractor:
    """Resolve the registered Extractor for a normalized lowercase suffix."""
    extractor = _EXTRACTOR_BY_SUFFIX.get(suffix)
    if extractor is None:
        raise UnsupportedFormatError(f"unsupported file type: {suffix}")
    return extractor


def check_content_signature(path: Path, extractor: Extractor, suffix: str) -> None:
    """Validate content signature before parsing (signature-first detection)."""
    try:
        with path.open("rb") as stream:
            head = stream.read(512)
    except Exception as error:
        raise MalformedInputError(f"unable to read file header: {error}") from error

    if not head:
        return

    if not extractor.magic:
        for sig in KNOWN_BINARY_SIGNATURES:
            if head.startswith(sig):
                raise MalformedInputError(
                    f"binary file content signature found in text file with suffix '{suffix}'"
                )
        return

    if extractor.name == "archive-tar":
        is_tar = (
            any(head.startswith(sig) for sig in (b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00"))
            or (len(head) >= 262 and head[257:262] == b"ustar")
            or tarfile.is_tarfile(path)
        )
        if not is_tar:
            raise MalformedInputError(
                f"file content signature does not match claimed format '{suffix}' "
                "(not a tar archive)"
            )
        return

    if not any(head.startswith(sig) for sig in extractor.magic):
        stripped = head.lstrip()
        if extractor.name in {"svg", "odf-flat"} and any(
            stripped.startswith(sig) for sig in extractor.magic
        ):
            pass
        else:
            detail = ""
            if extractor.name == "inpage" or suffix == ".inp":
                detail = " (not an InPage CFB compound document)"
            raise MalformedInputError(
                f"file content signature does not match claimed format '{suffix}'{detail}"
            )

    if extractor.name == "svg":
        with path.open("rb") as stream:
            svg_sample = stream.read(2048).lower()
        if b"<svg" not in svg_sample and b"<!doctype svg" not in svg_sample:
            raise MalformedInputError(
                f"file content signature does not match claimed format '{suffix}' "
                "(not an SVG document)"
            )

    if extractor.name == "webp-image" and head[8:12] != b"WEBP":
        raise MalformedInputError(
            f"file content signature does not match claimed format '{suffix}' "
            "(not a WEBP container)"
        )

    if extractor.name == "wav" and head[8:12] != b"WAVE":
        raise MalformedInputError(
            f"file content signature does not match claimed format '{suffix}' "
            "(not a WAVE container)"
        )


def export_registry_compatibility() -> list[dict[str, object]]:
    """Generate a structured summary of extractor format compatibility from the registry."""
    return [
        {
            "name": extractor.name,
            "version": extractor.version,
            "suffixes": sorted(extractor.suffixes),
            "media_types": sorted(extractor.media_types),
            "kind": extractor.kind,
            "cost": extractor.cost,
            "has_magic_signature": bool(extractor.magic),
        }
        for extractor in ALL_EXTRACTORS
    ]
