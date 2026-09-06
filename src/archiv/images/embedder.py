# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Perceptual image embedding, for finding near-duplicates of an image.

There is no text embedding here and no semantic search. What used to provide them was a
nineteen-entry colour lookup table with a hash-scatter fallback; step S10 deleted it.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from PIL import Image


def _pixel_rgb(val: object) -> tuple[int, int, int]:
    if isinstance(val, tuple) and len(val) >= 3:
        return int(val[0]), int(val[1]), int(val[2])
    if isinstance(val, (int, float)):
        iv = int(val)
        return iv, iv, iv
    return 0, 0, 0


def normalize_vector(vec: list[float]) -> list[float]:
    """Return unit L2-normalized vector."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 1e-12:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


class ImageEmbedder(Protocol):
    """What this package needs from an embedder: a vector for an image, and nothing else."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_image(self, image_path: Path) -> list[float]: ...


class PerceptualFeatureEmbedder:
    """Deterministic, zero-dependency perceptual embedder for images.

    Extracts multi-scale spatial colour moments, luminance statistics and directional
    gradients across a normalised spatial grid, into a 128-dimensional unit vector.

    There is no text side, and there was never a working one. What used to live here was
    a nineteen-entry colour lookup table with a hash-scatter fallback for every other
    word, so any string at all produced a vector and any query produced confident-looking
    ranked results. See `docs/plan/steps/S10.md` for what it did, measured.

    What this embedder can and cannot do is measured rather than assumed. It separates
    near-duplicates from unrelated images cleanly when the images differ in colour: true
    matches scored 0.9994 and above while unrelated pairs stayed at or below 0.9000. It
    does not separate them at all on light pages of dark text, where every pair -- the
    same page twice, or two unrelated invoices -- lands between 0.9993 and 1.0000. Global
    colour statistics dominate the vector, and two document scans have nearly identical
    ones whatever they say.
    """

    def __init__(self) -> None:
        self._model_name = "perceptual-v1"
        self._dimensions = 128

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_image(self, image_path: Path) -> list[float]:
        """Generate a 128-dim normalized embedding from an image file."""
        with Image.open(image_path) as raw_img:
            # Standardize image to 32x32 RGB
            img = raw_img.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)

        pixels: list[tuple[int, int, int]] = [
            _pixel_rgb(img.getpixel((x, y))) for y in range(32) for x in range(32)
        ]  # 1024 tuples of (R, G, B)
        # 1. Global channel statistics (mean, variance) -> 6 floats
        r_vals = [p[0] / 255.0 for p in pixels]
        g_vals = [p[1] / 255.0 for p in pixels]
        b_vals = [p[2] / 255.0 for p in pixels]

        mean_r = sum(r_vals) / 1024.0
        mean_g = sum(g_vals) / 1024.0
        mean_b = sum(b_vals) / 1024.0

        var_r = sum((x - mean_r) ** 2 for x in r_vals) / 1024.0
        var_g = sum((x - mean_g) ** 2 for x in g_vals) / 1024.0
        var_b = sum((x - mean_b) ** 2 for x in b_vals) / 1024.0

        features: list[float] = [mean_r, mean_g, mean_b, var_r, var_g, var_b]

        # 2. 4x4 spatial grid block statistics
        # 16 blocks x [mean_r, mean_g, mean_b, mean_lum] = 64 floats
        for by in range(4):
            for bx in range(4):
                block_r: list[float] = []
                block_g: list[float] = []
                block_b: list[float] = []
                for y in range(by * 8, (by + 1) * 8):
                    for x in range(bx * 8, (bx + 1) * 8):
                        idx = y * 32 + x
                        p = pixels[idx]
                        block_r.append(p[0] / 255.0)
                        block_g.append(p[1] / 255.0)
                        block_b.append(p[2] / 255.0)
                br = sum(block_r) / 64.0
                bg = sum(block_g) / 64.0
                bb = sum(block_b) / 64.0
                blum = 0.299 * br + 0.587 * bg + 0.114 * bb
                features.extend([br, bg, bb, blum])

        # 3. Horizontal and vertical edge energy across 16 rows and 16 cols (32 floats)
        lum_grid = [
            [
                0.299 * (pixels[y * 32 + x][0] / 255.0)
                + 0.587 * (pixels[y * 32 + x][1] / 255.0)
                + 0.114 * (pixels[y * 32 + x][2] / 255.0)
                for x in range(32)
            ]
            for y in range(32)
        ]

        for i in range(16):
            y1, y2 = i * 2, i * 2 + 1
            h_diff = sum(abs(lum_grid[y1][x] - lum_grid[y2][x]) for x in range(32)) / 32.0
            features.append(h_diff)

        for j in range(16):
            x1, x2 = j * 2, j * 2 + 1
            v_diff = sum(abs(lum_grid[y][x1] - lum_grid[y][x2]) for y in range(32)) / 32.0
            features.append(v_diff)

        # 4. Aspect and center-vs-edge contrast (remaining 26 floats to reach 128)
        center_lum = sum(lum_grid[y][x] for y in range(10, 22) for x in range(10, 22)) / 144.0
        features.append(center_lum)

        # Frequency bands (sub-sampled cross differences)
        for step in (2, 4, 8, 16):
            diag1 = sum(abs(lum_grid[k][k] - lum_grid[k][31 - k]) for k in range(0, 32, step)) / (
                32.0 / step
            )
            features.append(diag1)

        # Pad to exactly 128 if needed or truncate
        while len(features) < 128:
            features.append(features[len(features) - 1] * 0.5)
        features = features[:128]

        return normalize_vector(features)


def get_default_image_embedder() -> ImageEmbedder:
    """Return configured or default perceptual image embedder."""
    return PerceptualFeatureEmbedder()
