# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Permissive face detection and embedding extraction."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

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


def _is_skin_tone(r: int, g: int, b: int) -> bool:
    """Heuristic for human/synthetic skin tone in RGB."""
    return r > 50 and g > 30 and b > 15 and r > g and r > b and (r - g) >= 10 and abs(r - g) > 5


def detect_faces_in_image(
    image_path: Path,
    object_sha256: str,
    source_name: str,
    min_confidence: float = 0.50,
) -> list[FaceDetection]:
    """Detect candidate face bounding boxes and generate normalized embeddings."""
    with Image.open(image_path) as raw_img:
        width, height = raw_img.width, raw_img.height
        img = raw_img.convert("RGB")

    # Downsample for detection scan (max 128x128 grid)
    scale_w = min(1.0, 128.0 / max(1, width))
    scale_h = min(1.0, 128.0 / max(1, height))
    sw = max(16, int(width * scale_w))
    sh = max(16, int(height * scale_h))
    scan_img = img.resize((sw, sh), Image.Resampling.BILINEAR)

    # Detect face-like skin tone mask
    skin: list[list[bool]] = [
        [_is_skin_tone(*_pixel_rgb(scan_img.getpixel((x, y)))) for x in range(sw)]
        for y in range(sh)
    ]
    visited: list[list[bool]] = [[False] * sw for _ in range(sh)]

    detections: list[FaceDetection] = []

    # Connected component analysis for face regions
    min_comp_pixels = max(15, int(sw * sh * 0.005))

    for y in range(sh):
        for x in range(sw):
            if not skin[y][x] or visited[y][x]:
                continue

            comp_pixels: list[tuple[int, int]] = []
            queue: list[tuple[int, int]] = [(x, y)]
            visited[y][x] = True

            while queue:
                qx, qy = queue.pop()
                comp_pixels.append((qx, qy))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = qx + dx, qy + dy
                    if 0 <= nx < sw and 0 <= ny < sh and skin[ny][nx] and not visited[ny][nx]:
                        visited[ny][nx] = True
                        queue.append((nx, ny))

            if len(comp_pixels) < min_comp_pixels:
                continue

            min_x = min(p[0] for p in comp_pixels)
            max_x = max(p[0] for p in comp_pixels)
            min_y = min(p[1] for p in comp_pixels)
            max_y = max(p[1] for p in comp_pixels)

            cw = max_x - min_x + 1
            ch = max_y - min_y + 1
            aspect = ch / float(cw)

            # Face aspect ratio typically between 0.6 and 2.2
            if not (0.6 <= aspect <= 2.2):
                continue

            box_area = cw * ch
            fill_ratio = len(comp_pixels) / float(box_area)
            # Oval / convex face occupies ~40% to ~98% of its bounding rectangle
            if not (0.35 <= fill_ratio <= 0.98):
                continue

            orig_x0 = max(0.0, (min_x / float(sw)) * width)
            orig_y0 = max(0.0, (min_y / float(sh)) * height)
            orig_x1 = min(float(width), ((max_x + 1) / float(sw)) * width)
            orig_y1 = min(float(height), ((max_y + 1) / float(sh)) * height)

            conf = round(min(0.99, 0.50 + fill_ratio * 0.45), 3)
            if conf < min_confidence:
                continue

            bbox = [
                round(orig_x0, 1),
                round(orig_y0, 1),
                round(orig_x1, 1),
                round(orig_y1, 1),
            ]

            # Extract face crop and compute embedding
            box_tuple = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
            crop = img.crop(box_tuple).resize((32, 32), Image.Resampling.BILINEAR)
            embedding = compute_face_embedding(crop)

            detections.append(
                FaceDetection(
                    face_id=uuid4().hex[:16],
                    object_sha256=object_sha256,
                    source_name=source_name,
                    bbox=bbox,
                    confidence=conf,
                    embedding=embedding,
                )
            )

    return detections


def compute_face_embedding(face_crop: Image.Image) -> list[float]:
    """Compute a 64-dimensional unit L2-normalized face embedding."""
    crop = face_crop.convert("RGB").resize((32, 32))
    pixels: list[tuple[int, int, int]] = [
        _pixel_rgb(crop.getpixel((x, y))) for y in range(32) for x in range(32)
    ]

    features: list[float] = []

    # 1. 4 horizontal strip color ratios and luminance (16 features)
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

    # 2. Eye strip vs mouth strip color & lum contrast (8 features)
    eye_pixels = pixels[8 * 32 : 14 * 32]
    mouth_pixels = pixels[20 * 32 : 26 * 32]
    eye_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in eye_pixels) / (
        len(eye_pixels) * 255.0
    )
    mouth_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in mouth_pixels) / (
        len(mouth_pixels) * 255.0
    )
    features.append(eye_lum - mouth_lum)

    # 3. 4x4 spatial luminance grid relative to mean lum (16 features)
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

    # Pad to 64
    while len(features) < 64:
        features.append(0.0)
    features = features[:64]

    mean_f = sum(features) / 64.0
    centered = [f - mean_f for f in features]
    return normalize_vector(centered)
