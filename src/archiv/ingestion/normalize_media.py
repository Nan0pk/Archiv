"""Image and audio metadata normalization."""

from __future__ import annotations

import wave
from pathlib import Path

from PIL import ExifTags, Image, IptcImagePlugin

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.ingestion.limits import check_image, check_image_frames


def normalize_image(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    kind: str,
) -> NormalizedDocument:
    with Image.open(path) as image:
        check_image(image.width, image.height)
        n_frames = getattr(image, "n_frames", 1)
        check_image_frames(n_frames)
        image.verify()

    segments: list[NormalizedSegment] = []
    with Image.open(path) as image:
        n_frames = getattr(image, "n_frames", 1)
        check_image_frames(n_frames)
        metadata: dict[str, object] = {
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
            "format": image.format or kind.upper(),
            "frames": n_frames,
        }

        image_meta: dict[str, object] = {}
        exif_dict: dict[str, object] = {}
        try:
            exif = image.getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                    if isinstance(value, (bytes, bytearray)):
                        val_str = value.decode("utf-8", errors="replace").strip("\x00")
                        if val_str:
                            exif_dict[tag_name] = val_str
                    elif isinstance(value, (int, float, str, bool)):
                        exif_dict[tag_name] = value
        except Exception:
            pass

        if exif_dict:
            image_meta["exif"] = exif_dict
            description = exif_dict.get("ImageDescription")
            if isinstance(description, str) and description.strip():
                segments.append(
                    NormalizedSegment(
                        locator={"metadata": "exif.description"},
                        text=description.strip(),
                    )
                )

        iptc_dict: dict[str, object] = {}
        try:
            iptc_info = IptcImagePlugin.getiptcinfo(image)
            if iptc_info:
                for k, v in iptc_info.items():
                    key_name = str(k)
                    if isinstance(v, bytes):
                        v_str = v.decode("utf-8", errors="replace").strip("\x00")
                        if v_str:
                            iptc_dict[key_name] = v_str
                    else:
                        items = [item.decode("utf-8", errors="replace").strip("\x00") for item in v]
                        iptc_dict[key_name] = items
        except Exception:
            pass

        if iptc_dict:
            image_meta["iptc"] = iptc_dict
            caption_val = (
                iptc_dict.get("(2, 120)")
                or iptc_dict.get("caption")
                or iptc_dict.get("caption/abstract")
            )
            if isinstance(caption_val, str) and caption_val.strip():
                segments.append(
                    NormalizedSegment(
                        locator={"metadata": "iptc.caption"},
                        text=caption_val.strip(),
                    )
                )

        try:
            raw_xmp = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
            if isinstance(raw_xmp, bytes):
                image_meta["xmp"] = raw_xmp.decode("utf-8", errors="replace")
            elif isinstance(raw_xmp, str):
                image_meta["xmp"] = raw_xmp
        except Exception:
            pass

        if image_meta:
            metadata["image"] = image_meta

    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="image",
        source_name=source_name,
        segments=segments,
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
        metadata: dict[str, object] = {
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
