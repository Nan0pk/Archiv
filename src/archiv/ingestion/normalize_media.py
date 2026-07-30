"""Image and audio metadata normalization."""

from __future__ import annotations

import wave
from pathlib import Path

from PIL import Image

from archiv.contracts import NormalizedDocument


def normalize_image(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    kind: str,
) -> NormalizedDocument:
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        metadata = {
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "format": image.format or kind.upper(),
        }
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="image",
        source_name=source_name,
        metadata=metadata,
    )


def normalize_wav(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    with wave.open(str(path), "rb") as audio:
        sample_rate = audio.getframerate()
        frames = audio.getnframes()
        metadata = {
            "channels": audio.getnchannels(),
            "sample_width": audio.getsampwidth(),
            "sample_rate": sample_rate,
            "frames": frames,
            "duration_seconds": frames / sample_rate,
        }
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="audio",
        source_name=source_name,
        metadata=metadata,
    )
