# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Pluggable image and text embedding generation for semantic search and duplicate detection."""

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
    """Protocol for multimodal image and text embedding providers."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_image(self, image_path: Path) -> list[float]: ...

    def embed_text(self, text: str) -> list[float]: ...


class PerceptualFeatureEmbedder:
    """Deterministic, zero-dependency perceptual vision and text embedder.

    Extracts multi-scale spatial color moments, luminance statistics,
    and directional gradients across a normalized spatial grid.
    Produces a 128-dimensional normalized embedding vector.
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

    def embed_text(self, text: str) -> list[float]:
        """Generate a 128-dim normalized embedding vector from semantic query text."""
        tokens = text.lower().replace("-", " ").replace("_", " ").split()
        if not tokens:
            return [0.0] * 128

        vec = [0.0] * 128

        # Color and luminance priors
        color_map: dict[str, tuple[float, float, float]] = {
            "red": (1.0, 0.0, 0.0),
            "crimson": (0.9, 0.1, 0.1),
            "maroon": (0.5, 0.0, 0.0),
            "green": (0.0, 1.0, 0.0),
            "darkgreen": (0.0, 0.5, 0.0),
            "lime": (0.2, 0.9, 0.1),
            "blue": (0.0, 0.0, 1.0),
            "navy": (0.0, 0.0, 0.5),
            "cyan": (0.0, 1.0, 1.0),
            "magenta": (1.0, 0.0, 1.0),
            "yellow": (1.0, 1.0, 0.0),
            "orange": (1.0, 0.5, 0.0),
            "white": (1.0, 1.0, 1.0),
            "bright": (0.9, 0.9, 0.9),
            "light": (0.8, 0.8, 0.8),
            "black": (0.05, 0.05, 0.05),
            "dark": (0.1, 0.1, 0.1),
            "gray": (0.5, 0.5, 0.5),
            "grey": (0.5, 0.5, 0.5),
        }

        matched_color = False
        for token in tokens:
            if token in color_map:
                r, g, b = color_map[token]
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                vec[0] = r
                vec[1] = g
                vec[2] = b
                for b_idx in range(6, 70, 4):
                    vec[b_idx] = r
                    vec[b_idx + 1] = g
                    vec[b_idx + 2] = b
                    vec[b_idx + 3] = lum
                vec[102] = lum
                matched_color = True
                break

        # Spatial cues (top, bottom, left, right, center)
        if any(t in tokens for t in ("top", "upper", "header")):
            for b_idx in range(6, 38, 4):
                vec[b_idx + 3] += 0.5
        if any(t in tokens for t in ("bottom", "lower", "footer")):
            for b_idx in range(38, 70, 4):
                vec[b_idx + 3] += 0.5
        if any(t in tokens for t in ("center", "middle")):
            vec[102] += 0.5

        # Pattern / edge cues (lines, stripes, text, horizontal, vertical)
        if any(t in tokens for t in ("horizontal", "stripes", "rows", "lines")):
            for idx in range(70, 86):
                vec[idx] = 0.5
        if any(t in tokens for t in ("vertical", "columns", "bars")):
            for idx in range(86, 102):
                vec[idx] = 0.5

        if not matched_color:
            for token in tokens:
                seed = sum(ord(c) * (31**i) for i, c in enumerate(token[:10]))
                for k in range(16):
                    pos = (seed + k * 13) % 128
                    vec[pos] += 0.5

        return normalize_vector(vec)


def get_default_image_embedder() -> ImageEmbedder:
    """Return configured or default perceptual image embedder."""
    return PerceptualFeatureEmbedder()
