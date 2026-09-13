"""Acceptance tests for the clustering-only face fallback."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from archiv.faces.attributions import attribute_cluster
from archiv.faces.detector import detect_faces_in_image
from archiv.faces.storage import connect_face_index, face_index_path, pack_vector
from archiv.storage.layout import ArchivLayout


def _negative_images(tmp_path: Path) -> list[Path]:
    images: list[tuple[str, Image.Image]] = []

    vase = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(vase)
    draw.ellipse((42, 22, 86, 102), fill=(190, 120, 75))
    images.append(("terracotta_vase.png", vase))

    table = Image.new("RGB", (128, 128), (150, 90, 45))
    draw = ImageDraw.Draw(table)
    draw.line((0, 32, 127, 32), fill=(95, 55, 25), width=4)
    draw.line((0, 92, 127, 92), fill=(105, 60, 30), width=3)
    images.append(("wooden_table.png", table))

    sunset = Image.new("RGB", (128, 128), (235, 125, 70))
    draw = ImageDraw.Draw(sunset)
    draw.rectangle((0, 78, 127, 127), fill=(35, 55, 90))
    draw.ellipse((50, 50, 78, 78), fill=(255, 210, 95))
    images.append(("sunset.png", sunset))

    wall = Image.new("RGB", (128, 128), (165, 85, 60))
    draw = ImageDraw.Draw(wall)
    for y in range(0, 128, 20):
        draw.line((0, y, 127, y), fill=(220, 180, 150), width=2)
        offset = 10 if (y // 20) % 2 else 0
        for x in range(offset, 128, 32):
            draw.line((x, y, x, min(127, y + 20)), fill=(220, 180, 150), width=2)
    images.append(("brick_wall.png", wall))

    paths: list[Path] = []
    for name, image in images:
        path = tmp_path / name
        image.save(path)
        paths.append(path)
    return paths


def test_no_detections_on_vase_table_sunset_or_wall(tmp_path: Path) -> None:
    for path in _negative_images(tmp_path):
        detections = detect_faces_in_image(path, "0" * 64, path.name)
        assert detections == [], path.name


def test_no_name_candidate_is_ever_derived_from_a_filename(tmp_path: Path) -> None:
    layout = ArchivLayout.resolve(tmp_path / "archiv_home")
    db_path = face_index_path(layout)
    with connect_face_index(db_path) as conn:
        conn.execute(
            """
            INSERT INTO face_clusters
                (cluster_id, label, member_count, centroid, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("cluster-1", "Person 1", 1, pack_vector([0.0] * 64), "now", "now"),
        )
        conn.execute(
            """
            INSERT INTO faces
                (face_id, object_sha256, source_name, bbox_json, confidence,
                 embedding, cluster_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "face-1",
                "1" * 64,
                "Ada_Lovelace_portrait.png",
                "[0, 0, 10, 10]",
                0.9,
                pack_vector([0.0] * 64),
                "cluster-1",
                "now",
            ),
        )
        conn.commit()

    attribution = attribute_cluster(layout, "cluster-1")
    assert attribution is not None
    assert attribution.status == "unconfirmed"
    assert attribution.confirmed_name is None
    assert attribution.candidates == []
