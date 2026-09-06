"""Finding images that look like a given image, and refusing to pretend about the rest.

Step S10. Two things are asserted here and they pull in opposite directions, which is the
point: that the capability which survives actually works, and that the capability which
does not work is not quietly presented as working.

What went: the text query. It was a nineteen-entry colour lookup table with a hash-scatter
fallback, so every string produced a vector and every query produced a ranked table. In a
corpus with no people in it, "a photo of a person smiling" ranked a document first.

What stayed: finding near-duplicates of an image. It works on images that differ in
colour, and it does not work at all on light pages of dark text. Both halves of that are
measured below rather than asserted, because the measurement is the only reason anyone
should believe either.

Every fixture here is generated at test time. Nothing is committed.
"""

from __future__ import annotations

import ast
import json
from itertools import combinations
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageEnhance
from typer.testing import CliRunner

from archiv.cli import app
from archiv.images.embedder import get_default_image_embedder
from archiv.images.index import rebuild_image_index
from archiv.images.search import MATCH_FLOOR, NotAnImageError, find_similar_images
from archiv.ingestion.service import ingest_file

runner = CliRunner()

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "archiv"


def colourful(seed: int, size: tuple[int, int] = (320, 320)) -> Image.Image:
    """An image with a strong, distinctive colour character."""

    palette = [(240, 90, 30), (20, 110, 45), (20, 60, 160)]
    base = palette[seed % len(palette)]
    img = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(img)
    if seed % 3 == 0:
        draw.ellipse([40, 40, size[0] - 40, size[1] - 40], fill=(255, 230, 120))
    elif seed % 3 == 1:
        for x in range(0, size[0], 40):
            draw.rectangle([x, 0, x + 20, size[1]], fill=(240, 240, 200))
    else:
        for x in range(0, size[0], 64):
            for y in range(0, size[1], 64):
                draw.rectangle([x, y, x + 32, y + 32], fill=(10, 10, 40))
    return img


def outdoor_scene(seed: int) -> Image.Image:
    """Plainly different pictures that share a colour character: sky over ground.

    The middle ground, and the class the first version of this test left out. Its two
    classes were at opposite extremes -- three maximally distinct palettes, and near-white
    pages of dark text -- so the measurement flattered the floor and the limitation was
    written in terms of document scans. Real photograph archives look like this instead.
    """

    img = Image.new("RGB", (320, 320), (120, 170, 225))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 200, 320, 320], fill=(70, 140, 60))
    if seed % 3 == 0:
        draw.rectangle([90, 120, 230, 210], fill=(190, 170, 150))
        draw.polygon([(80, 120), (240, 120), (160, 60)], fill=(150, 70, 60))
    elif seed % 3 == 1:
        draw.rectangle([150, 140, 170, 210], fill=(110, 80, 50))
        draw.ellipse([100, 70, 220, 170], fill=(40, 120, 50))
    else:
        for x in (110, 160, 210):
            draw.ellipse([x - 12, 130, x + 12, 154], fill=(225, 195, 170))
            draw.rectangle([x - 14, 154, x + 14, 210], fill=(60, 70, 130))
    return img


def document_page(seed: int) -> Image.Image:
    """A light page of dark text, which is what a scanned document looks like."""

    img = Image.new("RGB", (420, 560), (250, 249, 246))
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), ["INVOICE 4471", "CONTRACT OF SALE", "MEDICAL REPORT"][seed % 3])
    for index, y in enumerate(range(70, 520, 20 + seed * 6)):
        width = 140 + (index * 37 * (seed + 1)) % 240
        draw.line([(24, y), (24 + width, y)], fill=(60, 60, 60), width=2)
    return img


def filed_four_ways(base: Image.Image, directory: Path, tag: str) -> list[Path]:
    """One image as it would arrive filed four times: the case this capability is for."""

    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    original = directory / f"{tag}-original.png"
    base.save(original)
    paths.append(original)
    recompressed = directory / f"{tag}-recompressed.jpg"
    base.save(recompressed, quality=72)
    paths.append(recompressed)
    smaller = directory / f"{tag}-half-size.png"
    # The Pillow stubs leave `resize` partially unknown. `tests/test_image_embeddings.py`
    # turns that check off for the whole file; narrowing it to the one call keeps the rest
    # of this file strictly checked.
    base.resize((base.width // 2, base.height // 2)).save(smaller)  # pyright: ignore[reportUnknownMemberType]
    paths.append(smaller)
    brighter = directory / f"{tag}-brighter.png"
    ImageEnhance.Brightness(base).enhance(1.12).save(brighter)
    paths.append(brighter)
    return paths


def cosine(first: list[float], second: list[float]) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


def separation(groups: dict[str, list[Path]]) -> tuple[float, float]:
    """The lowest true-match score and the highest unrelated score, across groups."""

    embedder = get_default_image_embedder()
    vectors = {path: embedder.embed_image(path) for paths in groups.values() for path in paths}
    within = [
        cosine(vectors[a], vectors[b])
        for paths in groups.values()
        for a, b in combinations(paths, 2)
    ]
    names = list(groups)
    across = [
        cosine(vectors[a], vectors[b])
        for i in range(len(names))
        for j in range(i + 1, len(names))
        for a in groups[names[i]]
        for b in groups[names[j]]
    ]
    return min(within), max(across)


def test_no_text_query_surface_remains() -> None:
    """Not weakened, not defaulted away: gone, so a text query cannot be reached at all.

    Checked against the source rather than by calling it, because the failure being
    guarded is a surface coming back, and a test that calls the thing it wants absent
    cannot tell "removed" from "still there but returning nothing".
    """

    embedder_source = (SOURCE_ROOT / "images" / "embedder.py").read_text(encoding="utf-8")
    search_source = (SOURCE_ROOT / "images" / "search.py").read_text(encoding="utf-8")
    cli_source = (SOURCE_ROOT / "images" / "cli.py").read_text(encoding="utf-8")

    # No function of that name is defined or declared anywhere in the image code.
    for name, source in (
        ("embedder.py", embedder_source),
        ("search.py", search_source),
        ("cli.py", cli_source),
    ):
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert "embed_text" not in defined, f"embed_text is still defined in {name}"

    # The colour lookup table that was the whole of it is gone too.
    for word in ("crimson", "maroon", "turquoise"):
        assert word not in embedder_source, (
            f"the colour lookup table is still present in embedder.py ({word!r})"
        )

    # And the embedder object really has no such attribute at runtime.
    assert not hasattr(get_default_image_embedder(), "embed_text")

    # The command is renamed, and the old name is not an alias for the new one.
    assert '"find-similar"' in cli_source
    assert '@images_app.command("search")' not in cli_source


def test_image_to_image_similarity_still_works(tmp_path: Path) -> None:
    """The capability that survives has to actually do the thing it is kept for."""

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    groups = {f"g{seed}": filed_four_ways(colourful(seed), corpus, f"g{seed}") for seed in range(3)}
    for paths in groups.values():
        for path in paths:
            ingest_file(path, home=home)
    assert rebuild_image_index(home=home).object_count == 12

    # Each copy finds its own group, and nothing from another group, above the floor.
    for tag, paths in groups.items():
        results = find_similar_images(paths[0], home=home, top_k=12)
        assert results, f"{tag}: an image found nothing that looks like it, including itself"
        returned = {result.source_name for result in results}
        assert returned <= {path.name for path in paths}, (
            f"{tag}: an unrelated image came back above the floor: {returned}"
        )
        assert len(returned) >= 2, f"{tag}: the other copies of the same image were not found"

    # A path that is not an image is refused, rather than becoming a text query -- which
    # is what a mistyped filename used to do.
    not_an_image = tmp_path / "typo.png"
    with pytest.raises(NotAnImageError, match="no such image"):
        find_similar_images(not_an_image, home=home)


def test_results_below_the_match_floor_return_nothing(tmp_path: Path) -> None:
    """A weak match is not a match, and must not be shown as a ranked row."""

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # Two images with deliberately different colour character, and no near-duplicates.
    first, second = corpus / "one.png", corpus / "two.png"
    colourful(0).save(first)
    colourful(1).save(second)
    for path in (first, second):
        ingest_file(path, home=home)
    rebuild_image_index(home=home)

    # At the default floor, an image finds itself and not the other one.
    results = find_similar_images(first, home=home, top_k=10)
    assert [result.source_name for result in results] == ["one.png"]

    # Drop the floor to nothing and the unrelated image reappears -- which is what the
    # command used to do by default, presenting it as a ranked result with a score.
    unfloored = find_similar_images(first, home=home, top_k=10, min_score=-1.0)
    assert len(unfloored) == 2
    assert unfloored[1].score < MATCH_FLOOR

    # Raise the floor above everything and nothing comes back at all, rather than the
    # best of a bad set.
    assert find_similar_images(first, home=home, top_k=10, min_score=1.0001) == []

    # The command says nothing was found, and does not print an empty table. Asked with
    # an image that is not in this archive and looks like nothing in it, there is no
    # match to show -- which is the ordinary way a person meets an empty result.
    stranger = tmp_path / "stranger.png"
    colourful(2).save(stranger)
    shown = runner.invoke(
        app, ["images", "find-similar", "--image", str(stranger), "--home", str(home)]
    )
    assert shown.exit_code == 0
    assert "Nothing in this archive looks like" in shown.output


def test_the_match_floor_comes_from_a_measurement_not_a_guess(tmp_path: Path) -> None:
    """The floor must keep every true match and admit no unrelated pair it can exclude.

    Measured across three content classes, not one. An earlier version of this test used
    only the two extremes -- maximally distinct palettes and near-white text pages -- and
    a floor of 0.99 sat comfortably inside the gap it found. Review generated the middle
    ground and that floor admitted a quarter of the unrelated pairs: a photograph of a
    tree came back as a match for a photograph of three people. The fixtures had been
    flattering, so the floor was wrong and so was the sentence written under it.

    What the numbers say now, and what the shipped floor is checked against:

    | Content | Lowest true match | Highest unrelated |
    |---|---|---|
    | Maximally distinct palettes | 0.9994 | 0.9000 |
    | Pictures sharing a colour character | 1.0000 | 0.9903 |
    | Light pages of dark text | 1.0000 | 1.0000 |
    """

    classes = {
        "distinct palettes": {
            f"p{seed}": filed_four_ways(colourful(seed), tmp_path / "flat", f"p{seed}")
            for seed in range(3)
        },
        "shared colour character": {
            f"o{seed}": filed_four_ways(outdoor_scene(seed), tmp_path / "outdoor", f"o{seed}")
            for seed in range(3)
        },
    }

    for label, groups in classes.items():
        lowest_true_match, highest_unrelated = separation(groups)
        # Every true match survives the floor. A floor that loses duplicates is worse
        # than none: the whole point is finding them.
        assert lowest_true_match >= MATCH_FLOOR, (
            f"{label}: the floor {MATCH_FLOOR} excludes a genuine duplicate at "
            f"{lowest_true_match:.4f}"
        )
        # And no unrelated pair gets through, in every class where the two populations
        # are separable at all.
        assert highest_unrelated < MATCH_FLOOR, (
            f"{label}: the floor {MATCH_FLOOR} admits an unrelated pair at {highest_unrelated:.4f}"
        )

    # The floor sits between the two populations of the tightest separable class rather
    # than merely above the easiest one.
    tightest = min(separation(groups)[0] - separation(groups)[1] for groups in classes.values())
    assert tightest > 0, "no class separates at all, so no floor is defensible"

    # The figures written into the constant are the figures just measured, not numbers
    # that were true once. A recorded measurement that can drift while the test passes
    # is a claim nobody is checking.
    source = (SOURCE_ROOT / "images" / "search.py").read_text(encoding="utf-8")
    assert "Measured, not chosen" in source
    for groups in classes.values():
        lowest_true_match, highest_unrelated = separation(groups)
        assert f"{lowest_true_match:.4f}" in source, (
            f"{lowest_true_match:.4f} was measured but is not the figure recorded in "
            "search.py; re-record it rather than leaving a stale number"
        )
        assert f"{highest_unrelated:.4f}" in source, (
            f"{highest_unrelated:.4f} was measured but is not the figure recorded in "
            "search.py; re-record it rather than leaving a stale number"
        )


def test_low_colour_variance_pages_are_not_discriminated(tmp_path: Path) -> None:
    """The limitation, asserted so it cannot become a sentence somebody stops believing.

    On light pages of dark text this embedder does not separate a duplicate from an
    unrelated page: every pair lands within a whisker of 1.0. That is the case the step
    named to justify keeping the capability -- the same scan filed in four folders -- and
    it is the one case the capability cannot serve. A floor cannot help: there is no gap
    to put one in.
    """

    groups = {
        f"doc{seed}": filed_four_ways(document_page(seed), tmp_path / "docs", f"doc{seed}")
        for seed in range(3)
    }
    lowest_true_match, highest_unrelated = separation(groups)

    # There is no usable gap. Both populations sit on top of each other near 1.0.
    assert lowest_true_match - highest_unrelated < 0.01, (
        "document pages now separate, which would be an improvement -- update this test "
        "and the claims in docs/plan/steps/S10.md and docs/known-issues.md"
    )
    assert highest_unrelated > 0.99, (
        "unrelated document pages no longer score near 1.0; re-measure the floor"
    )

    # So the shipped floor admits unrelated pages, and that is a property of the
    # embedder rather than a bug in the floor.
    assert highest_unrelated > MATCH_FLOOR

    # And it shows up through the command: an unrelated page is returned as a match.
    home = tmp_path / "home"
    for paths in groups.values():
        for path in paths:
            ingest_file(path, home=home)
    rebuild_image_index(home=home)
    results = find_similar_images(groups["doc0"][0], home=home, top_k=20)
    returned = {result.source_name for result in results}
    own_group = {path.name for path in groups["doc0"]}
    assert returned - own_group, (
        "unrelated document pages were excluded, which would mean the embedder has "
        "improved; re-measure and update the recorded limitation"
    )

    # The command warns about exactly this rather than leaving the reader to find out.
    shown = runner.invoke(
        app, ["images", "find-similar", "--image", str(groups["doc0"][0]), "--home", str(home)]
    )
    assert shown.exit_code == 0
    # Collapsed, because the console wraps the footer and the phrase spans two lines.
    flowed = " ".join(shown.output.split())
    assert "not a calibrated confidence" in flowed
    assert "light pages of dark text" in flowed

    # The machine-readable surface carries the scores without the prose, so a caller
    # cannot be warned there -- which is why the limitation is in known-issues too.
    machine = runner.invoke(
        app,
        [
            "images",
            "find-similar",
            "--image",
            str(groups["doc0"][0]),
            "--home",
            str(home),
            "--json",
        ],
    )
    assert machine.exit_code == 0
    assert isinstance(json.loads(machine.output), list)
