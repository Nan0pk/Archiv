"""Face embedding support while automatic detection is disabled."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from archiv.faces.contracts import FaceDetection
from archiv.images.embedder import normalize_vector


def _pixel_rgb(val: object) -> tuple[int, int, int]:
    if isinstance(val, tuple) and len(val) >= 3:
        return int(val[0]), int(val[1]), int(val[2])
    if isinstance(val, (int, float)):
        iv = int(val)
        return iv, iv, iv
    return 0, 0, 0


def detect_faces_in_image(
    image_path: Path,
    object_sha256: str,
    source_name: str,
    min_confidence: float = 0.50,
) -> list[FaceDetection]:
    """Return no automatic detections until S24 supplies a real face detector.

    The former skin-colour connected-component heuristic could report ordinary objects
    as people and assigned a made-up confidence value. Existing face-index records can
    still be clustered, erased, and explicitly confirmed; new automatic detections are
    deliberately refused rather than guessed.
    """
    del image_path, object_sha256, source_name, min_confidence
    return []


def compute_face_embedding(face_crop: Image.Image) -> list[float]:
    """Compute a 64-dimensional unit L2-normalized face embedding."""
    crop = face_crop.convert("RGB").resize((32, 32))
    pixels: list[tuple[int, int, int]] = [
        _pixel_rgb(crop.getpixel((x, y))) for y in range(32) for x in range(32)
    ]

    features: list[float] = []

    for strip_idx in range(4):
        strip_pixels = pixels[strip_idx * 256 : (strip_idx + 1) * 256]
        r_m = sum(p[0] for p in strip_pixels) / 256.0
        g_m = sum(p[1] for p in strip_pixels) / 256.0
        b_m = sum(p[2] for p in strip_pixels) / 256.0
        tot = max(1.0, r_m + g_m + b_m)
        features.extend(
            [
                (r_m - g_m) / tot,
                (r_m - b_m) / tot,
                (g_m - b_m) / tot,
                (0.299 * r_m + 0.587 * g_m + 0.114 * b_m) / 255.0 - 0.5,
            ]
        )

    eye_pixels = pixels[8 * 32 : 14 * 32]
    mouth_pixels = pixels[20 * 32 : 26 * 32]
    eye_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in eye_pixels) / (
        len(eye_pixels) * 255.0
    )
    mouth_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in mouth_pixels) / (
        len(mouth_pixels) * 255.0
    )
    features.append(eye_lum - mouth_lum)

    mean_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels) / (
        len(pixels) * 255.0
    )
    for by in range(4):
        for bx in range(4):
            b_pixels = [
                pixels[y * 32 + x]
                for y in range(by * 8, (by + 1) * 8)
                for x in range(bx * 8, (bx + 1) * 8)
            ]
            b_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in b_pixels) / (
                64.0 * 255.0
            )
            features.append(b_lum - mean_lum)

    while len(features) < 64:
        features.append(0.0)
    features = features[:64]

    mean_f = sum(features) / 64.0
    centered = [f - mean_f for f in features]
    return normalize_vector(centered)
