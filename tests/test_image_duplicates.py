"""S10B: the known-unsafe image duplicate surface refuses instead of guessing."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
from typer.testing import CliRunner

from archiv.cli import app
from archiv.images.index import rebuild_image_index
from archiv.ingestion.service import ingest_file

runner = CliRunner()


def document_page(seed: int) -> Image.Image:
    """Generate plainly different light document pages with similar colour balance."""

    img = Image.new("RGB", (420, 560), (250, 249, 246))
    draw = ImageDraw.Draw(img)
    headings = ["INVOICE 4471", "CONTRACT OF SALE", "MEDICAL REPORT"]
    draw.text((24, 24), headings[seed % len(headings)], fill=(35, 35, 35))
    for index, y in enumerate(range(70, 520, 20 + seed * 6)):
        width = 140 + (index * 37 * (seed + 1)) % 240
        draw.line([(24, y), (24 + width, y)], fill=(60, 60, 60), width=2)
    return img


def test_duplicates_command_refuses_the_old_similarity_method(tmp_path: Path) -> None:
    """A corpus like the known false-positive case can no longer produce a group."""

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    for seed in range(3):
        path = corpus / f"document-{seed}.png"
        document_page(seed).save(path)
        ingest_file(path, home=home)

    # Give the old command everything it needed to make its unsafe claim. The refusal
    # must happen even with a populated image index, not only because an index is absent.
    assert rebuild_image_index(home=home).object_count == 3

    result = runner.invoke(app, ["images", "duplicates", "--home", str(home)])

    assert result.exit_code == 2
    assert "temporarily unavailable" in result.output
    assert "could not safely distinguish some unrelated images" in result.output
    assert "No duplicate groups were reported" in result.output
    assert "Cluster #" not in result.output
    assert "Found 1 duplicate" not in result.output


def test_refusal_explains_why_no_duplicate_groups_are_reported(tmp_path: Path) -> None:
    """Machine-readable callers receive a refusal, not an empty-success lie."""

    result = runner.invoke(
        app,
        ["images", "duplicates", "--home", str(tmp_path / "home"), "--json"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "refused"
    assert payload["reason"] == "unsafe_similarity_method"
    assert payload["duplicate_groups"] == []
    assert (
        "previous method could not safely distinguish some unrelated images" in payload["message"]
    )
    assert "Exact-content duplicate tracking during ingestion is unchanged" in payload["message"]


def test_exact_content_duplicate_accounting_still_works(tmp_path: Path) -> None:
    """Disabling the unsafe image grouping must not disable byte-identity evidence."""

    home = tmp_path / "home"
    source = tmp_path / "same.png"
    document_page(0).save(source)

    first = ingest_file(source, home=home)
    second = ingest_file(source, home=home)

    assert not first.duplicate
    assert second.duplicate
    assert first.object_sha256 == second.object_sha256
