"""Tests for Capability Expansion Milestone 2 Phase 1: Raster formats & metadata.

Covers:
- Ingestion of GIF, BMP, TIFF, and WEBP images.
- Frame count limits (MAX_IMAGE_FRAMES).
- EXIF, IPTC, and XMP metadata extraction.
- Embedded captions/descriptions becoming searchable segments.
- Signature-first rejection across all raster formats.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.extractors import check_content_signature, get_extractor
from archiv.ingestion.formats import MalformedInputError
from archiv.ingestion.normalizers import normalize
from archiv.search import rebuild_search_index, search_documents


def test_raster_formats_ingest_successfully(tmp_path: Path) -> None:
    home = tmp_path / "home"

    # 1. GIF
    gif_path = tmp_path / "sample.gif"
    im_gif = Image.new("P", (100, 100), 0)
    im_gif.save(gif_path, format="GIF")
    res_gif = ingest_file(gif_path, home=home)
    assert res_gif.media_type == "image/gif"

    # 2. BMP
    bmp_path = tmp_path / "sample.bmp"
    im_bmp = Image.new("RGB", (100, 100), (255, 0, 0))
    im_bmp.save(bmp_path, format="BMP")
    res_bmp = ingest_file(bmp_path, home=home)
    assert res_bmp.media_type == "image/bmp"

    # 3. TIFF
    tiff_path = tmp_path / "sample.tiff"
    im_tiff = Image.new("RGB", (100, 100), (0, 255, 0))
    im_tiff.save(tiff_path, format="TIFF")
    res_tiff = ingest_file(tiff_path, home=home)
    assert res_tiff.media_type == "image/tiff"

    # 4. WEBP
    webp_path = tmp_path / "sample.webp"
    im_webp = Image.new("RGB", (100, 100), (0, 0, 255))
    im_webp.save(webp_path, format="WEBP")
    res_webp = ingest_file(webp_path, home=home)
    assert res_webp.media_type == "image/webp"


def test_raster_signature_rejection(tmp_path: Path) -> None:
    # A .gif file containing PNG magic bytes
    bad_gif = tmp_path / "bad.gif"
    bad_gif.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    ext_gif = get_extractor(".gif")
    with pytest.raises(MalformedInputError):
        check_content_signature(bad_gif, ext_gif, ".gif")

    # A .bmp file containing TIFF magic bytes
    bad_bmp = tmp_path / "bad.bmp"
    bad_bmp.write_bytes(b"II*\x00" + b"\x00" * 32)
    ext_bmp = get_extractor(".bmp")
    with pytest.raises(MalformedInputError):
        check_content_signature(bad_bmp, ext_bmp, ".bmp")

    # A .webp file with RIFF but not WEBP (e.g. WAVE)
    bad_webp = tmp_path / "bad.webp"
    bad_webp.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    ext_webp = get_extractor(".webp")
    with pytest.raises(MalformedInputError, match="not a WEBP container"):
        check_content_signature(bad_webp, ext_webp, ".webp")


def test_image_exceeding_max_frames_is_rejected(tmp_path: Path) -> None:
    bomb_gif = tmp_path / "frame_bomb.gif"
    first = Image.new("RGBA", (10, 10), (255, 0, 0, 255)).convert("P")
    others = [Image.new("RGBA", (10, 10), (0, i % 256, 0, 255)).convert("P") for i in range(105)]
    first.save(
        bomb_gif,
        save_all=True,
        append_images=others,
        format="GIF",
    )

    with pytest.raises(MalformedInputError, match="image frames limit exceeded"):
        normalize(bomb_gif, sha256_file(bomb_gif), source_name="frame_bomb.gif")


def test_image_exif_description_becomes_searchable_segment(tmp_path: Path) -> None:
    home = tmp_path / "home"
    img_path = tmp_path / "with_exif.jpg"

    marker_text = "ARCHIV-EXIF-MARKER-PHOTO-2026"
    im = Image.new("RGB", (100, 100), (200, 200, 200))
    exif = im.getexif()
    # ImageDescription tag ID is 270 (0x010E)
    exif[0x010E] = marker_text
    im.save(img_path, format="JPEG", exif=exif)

    result = ingest_file(img_path, home=home)
    norm = normalize(img_path, result.object_sha256, source_name="with_exif.jpg")
    assert any(
        seg.text == marker_text and seg.locator.get("metadata") == "exif.description"
        for seg in norm.segments
    )

    # Verify searchability
    rebuild_search_index(home=home)
    matches = search_documents(marker_text, home=home)
    assert len(matches) == 1
    assert matches[0].citation.source_name == "with_exif.jpg"
    assert matches[0].citation.locator == {"metadata": "exif.description"}


def test_multipage_tiff_derives_pages_and_metadata(tmp_path: Path) -> None:
    home = tmp_path / "home"
    tiff_path = tmp_path / "multipage.tiff"

    page1 = Image.new("RGB", (100, 100), (255, 0, 0))
    page2 = Image.new("RGB", (100, 100), (0, 0, 255))
    page1.save(
        tiff_path,
        save_all=True,
        append_images=[page2],
        format="TIFF",
    )

    result = ingest_file(tiff_path, home=home)
    norm = normalize(tiff_path, result.object_sha256, source_name="multipage.tiff")
    assert norm.metadata.get("frames") == 2

    # Derived artifacts check
    from archiv.ingestion.derive import derive
    from archiv.storage.database import ArchivDatabase
    from archiv.storage.layout import ArchivLayout

    layout = ArchivLayout.resolve(home)
    db = ArchivDatabase(layout.database)
    derive(
        original=tiff_path,
        digest=result.object_sha256,
        source_name="multipage.tiff",
        layout=layout,
        database=db,
        replace=True,
        normalized=norm,
    )

    derived_root = layout.derived_root(result.object_sha256)
    assert (derived_root / "previews" / "pages" / "page-0001.png").is_file()
    assert (derived_root / "previews" / "pages" / "page-0002.png").is_file()
    assert (derived_root / "previews" / "thumbnail.webp").is_file()


def test_svg_normalization_and_searchability(tmp_path: Path) -> None:
    from archiv.search.service import validate_citation

    home = tmp_path / "home"
    svg_path = tmp_path / "diagram.svg"

    marker_text = "ARCHIV-SVG-SEARCHABLE-TOKEN-2026"
    svg_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">
    <title>Architecture Diagram</title>
    <desc>Evidence graph pipeline overview</desc>
    <script>alert("malicious execution should never happen")</script>
    <g>
        <text x="20" y="50">{marker_text}</text>
        <path d="M 10 10 L 90 90" aria-label="Connecting line from input to output" />
    </g>
</svg>
"""
    svg_path.write_text(svg_content, encoding="utf-8")

    result = ingest_file(svg_path, home=home)
    assert result.media_type == "image/svg+xml"

    norm = normalize(svg_path, result.object_sha256, source_name="diagram.svg")
    assert norm.kind == "svg"

    # Verify script content is excluded
    assert not any("malicious execution" in seg.text for seg in norm.segments)

    # Verify extracted segments
    texts = {seg.text: seg.locator for seg in norm.segments}
    assert "Architecture Diagram" in texts
    assert texts["Architecture Diagram"] == {"element": "title[1]"}
    assert "Evidence graph pipeline overview" in texts
    assert texts["Evidence graph pipeline overview"] == {"element": "desc[1]"}
    assert marker_text in texts
    assert texts[marker_text] == {"element": "text[1]"}
    assert "Connecting line from input to output" in texts
    assert texts["Connecting line from input to output"] == {"element": "path[1].aria-label"}

    # Verify metadata
    assert norm.metadata.get("width") == "400"
    assert norm.metadata.get("height") == "300"
    assert norm.metadata.get("viewBox") == "0 0 400 300"
    assert norm.metadata.get("text_element_count") == 4

    # Verify search and citation
    rebuild_search_index(home=home)
    matches = search_documents(marker_text, home=home)
    assert len(matches) == 1
    assert matches[0].citation.source_name == "diagram.svg"
    assert matches[0].citation.locator == {"element": "text[1]"}
    val = validate_citation(matches[0].citation, home=home)
    assert val.valid


def test_svg_security_hardening(tmp_path: Path) -> None:
    # 1. Reject XXE entity injection
    xxe_svg = tmp_path / "xxe.svg"
    xxe_svg.write_text(
        '<!DOCTYPE svg [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>',
        encoding="utf-8",
    )
    with pytest.raises(MalformedInputError, match="forbidden"):
        normalize(xxe_svg, "deadbeef", source_name="xxe.svg")

    # 2. Reject billion laughs entity expansion bomb
    bomb_svg = tmp_path / "bomb.svg"
    bomb_svg.write_text(
        "<!DOCTYPE svg [\n"
        '<!ENTITY lol "lol">\n'
        '<!ENTITY lol2 "&lol;&lol;">\n'
        "]>\n"
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&lol2;</text></svg>',
        encoding="utf-8",
    )
    with pytest.raises(MalformedInputError, match="forbidden"):
        normalize(bomb_svg, "deadbeef", source_name="bomb.svg")

    # 3. Reject non-SVG root element
    from archiv.ingestion.normalize_svg import normalize_svg

    non_svg = tmp_path / "not_svg.svg"
    non_svg.write_text(
        '<?xml version="1.0"?>\n<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
        encoding="utf-8",
    )
    with pytest.raises(MalformedInputError, match="root XML element is not <svg>"):
        normalize_svg(non_svg, "deadbeef", source_name="not_svg.svg", media_type="image/svg+xml")
    with pytest.raises(MalformedInputError):
        normalize(non_svg, "deadbeef", source_name="not_svg.svg")

    # 4. Reject malformed XML syntax
    broken_svg = tmp_path / "broken.svg"
    broken_svg.write_text("<svg><text>unclosed", encoding="utf-8")
    with pytest.raises(MalformedInputError, match="malformed SVG XML"):
        normalize(broken_svg, "deadbeef", source_name="broken.svg")

    # 5. Reject binary disguised as SVG in signature check
    binary_svg = tmp_path / "binary.svg"
    binary_svg.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    ext_svg = get_extractor(".svg")
    with pytest.raises(MalformedInputError):
        check_content_signature(binary_svg, ext_svg, ".svg")
