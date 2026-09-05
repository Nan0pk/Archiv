# pyright: reportUnknownMemberType=false
"""Acceptance tests and benchmark for image embeddings, semantic search, and near-duplicates."""

from __future__ import annotations

import math
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from typer.testing import CliRunner

from archiv.cli import app
from archiv.images.embedder import (
    PerceptualFeatureEmbedder,
    normalize_vector,
)
from archiv.images.index import image_index_path, rebuild_image_index
from archiv.images.search import find_near_duplicates, search_images
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _make_solid_color_image(
    color: tuple[int, int, int], size: tuple[int, int] = (128, 128)
) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_pattern_image(
    bg_color: tuple[int, int, int],
    fg_color: tuple[int, int, int],
    shape: str = "rectangle",
    size: tuple[int, int] = (128, 128),
) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", size, color=bg_color)
    draw = ImageDraw.Draw(img)
    if shape == "rectangle":
        draw.rectangle((20, 20, 108, 108), fill=fg_color)
    elif shape == "circle":
        draw.ellipse((20, 20, 108, 108), fill=fg_color)
    elif shape == "horizontal_lines":
        for y in range(0, 128, 16):
            draw.line((0, y, 128, y), fill=fg_color, width=4)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_embedder_normalization_and_determinism(tmp_path: Path) -> None:
    embedder = PerceptualFeatureEmbedder()
    img_bytes = _make_pattern_image((255, 255, 255), (255, 0, 0), shape="circle")
    p1 = tmp_path / "red_circle.png"
    p1.write_bytes(img_bytes)

    vec1 = embedder.embed_image(p1)
    assert len(vec1) == 128
    norm = math.sqrt(sum(x * x for x in vec1))
    assert abs(norm - 1.0) < 1e-5, f"Vector norm {norm} is not unit length"

    # Determinism: identical file produces identical embedding
    vec2 = embedder.embed_image(p1)
    assert vec1 == vec2

    # Normalization helper handles zero-vector safely
    zero_vec = normalize_vector([0.0] * 128)
    assert zero_vec == [0.0] * 128


def test_rebuild_image_index_and_retrieval(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    # Create 3 distinct images
    red_path = tmp_path / "crimson_block.png"
    red_path.write_bytes(_make_solid_color_image((230, 20, 20)))

    blue_path = tmp_path / "navy_circle.png"
    blue_path.write_bytes(_make_pattern_image((240, 240, 240), (10, 20, 220), shape="circle"))

    green_path = tmp_path / "green_stripes.png"
    green_path.write_bytes(
        _make_pattern_image((20, 220, 30), (0, 100, 0), shape="horizontal_lines")
    )

    ingest_file(red_path, home=home)
    ingest_file(blue_path, home=home)
    ingest_file(green_path, home=home)

    layout = ArchivLayout.resolve(home)
    idx_path = image_index_path(layout)
    assert not idx_path.is_file()

    build_res = rebuild_image_index(home=home)
    assert build_res.object_count == 3
    assert idx_path.is_file()
    assert build_res.index_size_bytes > 0

    # 1. Text semantic search for 'red'
    results_red = search_images("red", home=home, top_k=3)
    assert len(results_red) == 3
    assert results_red[0].source_name == "crimson_block.png"
    assert results_red[0].score > results_red[1].score

    # 2. Text semantic search for 'blue'
    results_blue = search_images("blue", home=home, top_k=3)
    assert results_blue[0].source_name == "navy_circle.png"

    # 3. Image-to-image search with blue image path
    results_img = search_images(blue_path, home=home, top_k=3)
    assert results_img[0].source_name == "navy_circle.png"
    assert abs(results_img[0].score - 1.0) < 1e-4


def test_near_duplicate_detection(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    # Original image
    orig_bytes = _make_pattern_image((255, 255, 255), (0, 0, 255), shape="rectangle")
    img_orig = tmp_path / "scan_original.png"
    img_orig.write_bytes(orig_bytes)

    # Near duplicate: slightly resized version (e.g. 120x120 instead of 128x128)
    buf = BytesIO()
    with Image.open(img_orig) as img:
        img_resized = img.resize((120, 120))
        img_resized.save(buf, format="PNG")
    img_dup = tmp_path / "scan_copy_resized.png"
    img_dup.write_bytes(buf.getvalue())

    # Completely distinct image (yellow solid)
    img_diff = tmp_path / "document_yellow.png"
    img_diff.write_bytes(_make_solid_color_image((255, 240, 20)))

    ingest_file(img_orig, home=home)
    ingest_file(img_dup, home=home)
    ingest_file(img_diff, home=home)

    rebuild_image_index(home=home)

    # Detect duplicates with high similarity threshold
    groups = find_near_duplicates(threshold=0.98, home=home)
    assert len(groups) == 1
    group = groups[0]
    all_names = {group.lead_source_name} | {m.source_name for m in group.members}
    assert "scan_original.png" in all_names
    assert "scan_copy_resized.png" in all_names
    assert "document_yellow.png" not in all_names
    assert group.max_similarity >= 0.98


def test_index_rebuildability_and_wipe_safety(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    img_path = tmp_path / "pic.png"
    img_path.write_bytes(_make_solid_color_image((100, 100, 200)))
    ingest_file(img_path, home=home)

    # Initial build
    rebuild_image_index(home=home)
    layout = ArchivLayout.resolve(home)
    idx_path = image_index_path(layout)
    assert idx_path.is_file()

    res = search_images("blue", home=home)
    assert len(res) == 1

    # Wiping the rebuildable index must not crash queries or corrupt originals
    idx_path.unlink()
    assert not idx_path.is_file()
    assert search_images("blue", home=home) == []
    assert find_near_duplicates(home=home) == []

    # Rebuild recreates the index
    rebuild_image_index(home=home)
    assert idx_path.is_file()
    assert len(search_images("blue", home=home)) == 1


def test_image_embedding_benchmark(tmp_path: Path) -> None:
    """Benchmark index construction, storage footprint, and query latency."""
    home = tmp_path / "archiv_home"

    # Ingest a small synthetic corpus (10 distinct colored pattern images)
    palette = [
        ((255, 0, 0), "red"),
        ((0, 255, 0), "green"),
        ((0, 0, 255), "blue"),
        ((255, 255, 0), "yellow"),
        ((255, 0, 255), "magenta"),
        ((0, 255, 255), "cyan"),
        ((128, 0, 0), "maroon"),
        ((0, 128, 0), "darkgreen"),
        ((0, 0, 128), "navy"),
        ((128, 128, 128), "gray"),
    ]

    for color, name in palette:
        p = tmp_path / f"{name}.png"
        p.write_bytes(_make_solid_color_image(color))
        ingest_file(p, home=home)

    # Measure build performance
    t0 = time.monotonic()
    build_result = rebuild_image_index(home=home)
    build_duration = time.monotonic() - t0

    assert build_result.object_count == 10
    assert build_result.index_size_bytes < 100 * 1024  # Under 100 KB for 10 images
    throughput = 10.0 / max(0.0001, build_duration)
    assert throughput > 5.0, f"Indexing throughput {throughput:.1f} img/s is too slow"

    # Measure search query latency over 20 queries
    latencies: list[float] = []
    correct_top1 = 0

    for _color, name in palette:
        q_start = time.monotonic()
        results = search_images(name, home=home, top_k=3)
        latencies.append((time.monotonic() - q_start) * 1000.0)
        if results and name in results[0].source_name:
            correct_top1 += 1

    avg_latency_ms = sum(latencies) / len(latencies)
    recall_at_1 = correct_top1 / len(palette)

    # Verify latency bounds (< 50ms per query) and reasonable recall
    assert avg_latency_ms < 50.0, f"Average query latency {avg_latency_ms:.2f}ms exceeds 50ms limit"
    assert recall_at_1 >= 0.7, f"Recall@1 {recall_at_1:.2f} is below target 0.70"


def test_cli_images_subcommands(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(_make_solid_color_image((200, 50, 50)))
    ingest_file(img_path, home=home)

    # Status before index
    res_status0 = runner.invoke(app, ["images", "status", "--home", str(home), "--json"])
    assert res_status0.exit_code == 0
    assert '"status": "missing"' in res_status0.output

    # Rebuild index
    res_build = runner.invoke(app, ["images", "rebuild-index", "--home", str(home), "--json"])
    assert res_build.exit_code == 0
    assert '"object_count": 1' in res_build.output

    # Status after index
    res_status1 = runner.invoke(app, ["images", "status", "--home", str(home), "--json"])
    assert res_status1.exit_code == 0
    assert '"status": "ready"' in res_status1.output
    assert '"images_indexed": 1' in res_status1.output

    # Search CLI
    res_search = runner.invoke(app, ["images", "search", "red", "--home", str(home), "--json"])
    assert res_search.exit_code == 0
    assert "photo.png" in res_search.output

    # Duplicates CLI
    res_dup = runner.invoke(app, ["images", "duplicates", "--home", str(home), "--json"])
    assert res_dup.exit_code == 0
    assert res_dup.output.strip() == "[]"
