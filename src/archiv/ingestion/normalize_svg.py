"""Hardened local SVG XML normalization.

Extracts text from <text>, <title>, <desc>, and aria-label attributes.
Hardened against XXE and billion-laughs attacks by strictly disallowing
DTD entity declarations and external entity resolution.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.ingestion.formats import MalformedInputError
from archiv.ingestion.limits import MAX_INPUT_BYTES


def normalize_svg(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Safely extract searchable text elements and metadata from an SVG document."""

    if path.stat().st_size > MAX_INPUT_BYTES:
        raise MalformedInputError(f"SVG exceeds maximum input limit of {MAX_INPUT_BYTES} bytes")

    try:
        raw_bytes = path.read_bytes()
    except Exception as error:
        raise MalformedInputError(f"unable to read SVG file: {error}") from error

    lower = raw_bytes.lower()
    if b"<!entity" in lower:
        raise MalformedInputError("XML entity declarations are forbidden in SVG inputs")
    if b"<!doctype" in lower and (b"[" in lower or b"system" in lower or b"public" in lower):
        raise MalformedInputError(
            "external DTDs and entity declarations are forbidden in SVG inputs"
        )

    try:
        root = ET.fromstring(raw_bytes)
    except Exception as error:
        raise MalformedInputError(f"malformed SVG XML: {error}") from error

    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_tag.lower() != "svg":
        raise MalformedInputError("root XML element is not <svg>")

    segments: list[NormalizedSegment] = []
    element_counts: dict[str, int] = {}
    total_elements = 0

    for elem in root.iter():
        total_elements += 1
        tag = elem.tag.split("}")[-1].lower() if "}" in elem.tag else elem.tag.lower()
        if tag == "script":
            continue

        element_counts[tag] = element_counts.get(tag, 0) + 1
        idx = element_counts[tag]

        # Extract text content for text, title, and desc
        if tag in {"text", "title", "desc"}:
            text = "".join(elem.itertext()).strip()
            if text:
                segments.append(
                    NormalizedSegment(
                        locator={"element": f"{tag}[{idx}]"},
                        text=text,
                    )
                )

        # Extract aria-label attribute if present
        aria_label = elem.attrib.get("aria-label", "").strip()
        if aria_label:
            segments.append(
                NormalizedSegment(
                    locator={"element": f"{tag}[{idx}].aria-label"},
                    text=aria_label,
                )
            )

    metadata: dict[str, object] = {
        "width": root.attrib.get("width"),
        "height": root.attrib.get("height"),
        "viewBox": root.attrib.get("viewBox"),
        "total_elements": total_elements,
        "element_counts": element_counts,
        "text_element_count": len(segments),
    }

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="svg",
        source_name=source_name,
        segments=segments,
        metadata=metadata,
    )
