"""Presentation normalization."""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation

from archiv.contracts import NormalizedDocument, NormalizedSegment


def normalize_pptx(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    presentation = Presentation(str(path))
    segments: list[NormalizedSegment] = []
    for slide_number, slide in enumerate(presentation.slides, 1):
        for shape_number, shape in enumerate(slide.shapes, 1):
            text = getattr(shape, "text", "")
            if text:
                segments.append(
                    NormalizedSegment(
                        locator={"slide": slide_number, "shape": shape_number},
                        text=text,
                    )
                )
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="pptx",
        source_name=source_name,
        segments=segments,
        metadata={"slides": len(presentation.slides)},
    )
