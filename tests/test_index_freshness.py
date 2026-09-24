"""S13 acceptance: `add` must not leave the image index or entity graph stale.

Reproduced before this step: after `archiv add`, `archiv images status` reported a bare
count that could sit below the true number of image objects in the store, and neither the
image index nor the entity graph was touched by `add` at all -- only the FTS index was.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from archiv.cli import app
from archiv.images.embedder import ImageEmbedder, get_default_image_embedder
from archiv.images.index import (
    count_image_objects,
    rebuild_image_index,
    update_image_index,
)
from archiv.ingestion import ingest_file

runner = CliRunner()


def _make_solid_color_image(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


class _CountingEmbedder:
    """Wraps a real embedder and records every path it was actually asked to embed."""

    def __init__(self, inner: ImageEmbedder) -> None:
        self._inner = inner
        self.embedded_paths: list[Path] = []

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed_image(self, image_path: Path) -> list[float]:
        self.embedded_paths.append(image_path)
        return self._inner.embed_image(image_path)


def test_add_refreshes_the_image_index_and_graph(tmp_path: Path) -> None:
    home = tmp_path / "home"
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(_make_solid_color_image((10, 200, 30)))
    text_path = tmp_path / "note.txt"
    text_path.write_text("Minutes recorded by Jordan Alvarez for the archive.", encoding="utf-8")

    added = runner.invoke(app, ["add", str(tmp_path), "--home", str(home), "--json"])
    assert added.exit_code == 0, added.output

    status = runner.invoke(app, ["images", "status", "--home", str(home), "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["status"] == "ready"
    assert status_payload["images_indexed"] == 1
    assert status_payload["images_total"] == 1

    graph_stats = runner.invoke(app, ["graph", "stats", "--home", str(home), "--json"])
    assert graph_stats.exit_code == 0, graph_stats.output
    stats_payload = json.loads(graph_stats.output)
    assert stats_payload["total_nodes"] > 0


def test_status_reports_indexed_count_against_object_count(tmp_path: Path) -> None:
    home = tmp_path / "home"

    for i in range(3):
        path = tmp_path / f"early-{i}.png"
        path.write_bytes(_make_solid_color_image((i * 10, 50, 80)))
        ingest_file(path, home=home)
    rebuild_image_index(home=home)

    for i in range(4):
        path = tmp_path / f"late-{i}.png"
        path.write_bytes(_make_solid_color_image((10, i * 10, 150)))
        ingest_file(path, home=home)

    assert count_image_objects(home=home) == 7

    status = runner.invoke(app, ["images", "status", "--home", str(home), "--json"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert payload["status"] == "ready"
    assert payload["images_indexed"] == 3
    assert payload["images_total"] == 7

    text_status = runner.invoke(app, ["images", "status", "--home", str(home)])
    assert text_status.exit_code == 0, text_status.output
    assert "3 of 7" in text_status.output


def test_image_index_update_is_incremental(tmp_path: Path) -> None:
    home = tmp_path / "home"

    first = tmp_path / "first.png"
    first.write_bytes(_make_solid_color_image((100, 100, 100)))
    ingest_file(first, home=home)
    build = rebuild_image_index(home=home)
    assert build.object_count == 1

    second = tmp_path / "second.png"
    second.write_bytes(_make_solid_color_image((5, 250, 5)))
    second_result = ingest_file(second, home=home)

    counting = _CountingEmbedder(get_default_image_embedder())
    result = update_image_index(
        [second_result.object_sha256],
        home=home,
        embedder=counting,
    )

    # Only the newly added digest was embedded -- the already-indexed image was not
    # re-run through the model, unlike a full `rebuild_image_index`.
    assert len(counting.embedded_paths) == 1
    assert result.object_count == 2
