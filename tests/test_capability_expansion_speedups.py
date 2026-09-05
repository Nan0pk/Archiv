# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
"""Tests for capability expansion speedups #4–#7 (PR 108 Milestone)."""

from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.hashing import sha256_bytes, sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.visual_ocr import _split_batch_tsv
from archiv.search import rebuild_search_index, search_documents, update_search_index
from archiv.storage.integrity import inspect_home
from archiv.storage.layout import ArchivLayout
from archiv.user_cli import _add_sources, _candidates


def _make_multipage_tiff(texts: list[str]) -> bytes:
    frames = []
    for text in texts:
        img = Image.new("RGB", (300, 100), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 10), text, fill="black")
        frames.append(img)
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def test_incremental_search_index_add_and_update(tmp_path: Path) -> None:
    home = tmp_path / "home"
    doc1 = tmp_path / "doc1.txt"
    doc1.write_text("UniqueFirstWord in document one.\n", encoding="utf-8")

    res1 = ingest_file(doc1, home=home)
    build1 = update_search_index([res1.object_sha256], home=home)
    assert build1.object_count == 1
    assert build1.segment_count >= 1

    matches = search_documents("UniqueFirstWord", home=home)
    assert len(matches) == 1
    assert matches[0].citation.object_sha256 == res1.object_sha256

    # Add second document incrementally
    doc2 = tmp_path / "doc2.txt"
    doc2.write_text("UniqueSecondWord in document two.\n", encoding="utf-8")
    res2 = ingest_file(doc2, home=home)

    build2 = update_search_index([res2.object_sha256], home=home)
    assert build2.object_count == 2
    assert build2.segment_count >= 2

    # Both documents are searchable
    matches1 = search_documents("UniqueFirstWord", home=home)
    assert len(matches1) == 1
    matches2 = search_documents("UniqueSecondWord", home=home)
    assert len(matches2) == 1

    # Update document 1's segments in normalized JSON
    layout = ArchivLayout.resolve(home)
    norm_path = layout.derived_root(res1.object_sha256) / "normalized" / "document.json"
    doc = NormalizedDocument.model_validate_json(norm_path.read_text(encoding="utf-8"))
    doc.segments = [
        NormalizedSegment(locator={"line": 1}, text="ReplacedReplacementWord in document one.")
    ]
    norm_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    # Update index for doc1
    build_updated = update_search_index([res1.object_sha256], home=home)
    assert build_updated.object_count == 2

    # Old word is gone, new word is found
    assert len(search_documents("UniqueFirstWord", home=home)) == 0
    assert len(search_documents("ReplacedReplacementWord", home=home)) == 1

    # Full rebuild matches ground truth
    full_build = update_search_index(home=home, full=True)
    assert full_build.object_count == 2
    assert full_build.segment_count == build_updated.segment_count


def test_integrity_rehash_not_on_search_index_hot_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    doc = tmp_path / "doc.txt"
    doc.write_text("Content to search.\n", encoding="utf-8")
    res = ingest_file(doc, home=home)

    layout = ArchivLayout.resolve(home)
    original_file = layout.original_path(res.object_sha256)

    # In rebuild_search_index, original_file should NOT be hashed
    hashed_paths: list[Path] = []
    real_sha256_file = sha256_file

    def mock_sha256_file(path: Path) -> str:
        hashed_paths.append(path.resolve())
        return real_sha256_file(path)

    with patch("archiv.search.index.sha256_file", side_effect=mock_sha256_file):
        rebuild_search_index(home=home)

    # Verify original file was never hashed during search index rebuild
    assert original_file.resolve() not in hashed_paths

    # However, doctor/inspect_home DOES audit canonical originals
    report = inspect_home(home)
    assert report["ok"] is True
    assert report["canonical_objects"]["checked"] == 1
    assert report["canonical_objects"]["corrupt"] == 0


def test_parallel_add_sources_and_compound_suffixes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()

    # Create several files of different supported types
    (source_dir / "file1.txt").write_text("First file content.\n", encoding="utf-8")
    (source_dir / "file2.md").write_text("# Markdown file content\n", encoding="utf-8")
    (source_dir / "file3.rtf").write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Courier;}}\f0\fs24 RTF document content.\par}",
        encoding="ascii",
    )
    (source_dir / "file4_dup.txt").write_text("First file content.\n", encoding="utf-8")

    # Create a compound archive file inside the directory (.tar.gz)
    tar_gz_path = source_dir / "archive.tar.gz"
    with tarfile.open(tar_gz_path, "w:gz") as tar:
        inner_content = b"Archived inner text file.\n"
        ti = tarfile.TarInfo(name="inner.txt")
        ti.size = len(inner_content)
        tar.addfile(ti, io.BytesIO(inner_content))

    # Test candidate discovery includes compound suffix .tar.gz
    candidates, rejected = _candidates(source_dir)
    cand_names = [c.name for c in candidates]
    assert "archive.tar.gz" in cand_names
    assert "file1.txt" in cand_names
    assert "file2.md" in cand_names
    assert "file3.rtf" in cand_names
    assert "file4_dup.txt" in cand_names
    assert rejected == 0

    # Run _add_sources (parallel path since len(candidates) > 1)
    results, index, counts = _add_sources(source_dir, home=home, rebuild_derived=False)
    assert counts.supported >= 5
    assert counts.failed == 0
    assert counts.rejected == 0
    assert index is not None

    # Check deduplication: file1.txt and file4_dup.txt share the same content
    file1_hash = sha256_bytes(b"First file content.\n")
    dup_results = [r for r in results if r.object_sha256 == file1_hash]
    assert len(dup_results) == 2
    assert sum(not r.duplicate for r in dup_results) == 1
    assert sum(r.duplicate for r in dup_results) == 1

    # Check that search index finds both loose and archive content
    matches_inner = search_documents("Archived inner", home=home)
    assert len(matches_inner) >= 1

    matches_md = search_documents("Markdown", home=home)
    assert len(matches_md) >= 1


def test_split_batch_tsv_helper() -> None:
    sample_tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t20\t10\t95\tPageOneWord\n"
        "5\t2\t1\t1\t1\t1\t10\t10\t20\t10\t92\tPageTwoWord\n"
        "5\t2\t1\t1\t1\t2\t35\t10\t20\t10\t90\tSecondWord\n"
    )
    split = _split_batch_tsv(sample_tsv, 2)
    assert 1 in split
    assert 2 in split
    assert "PageOneWord" in split[1]
    assert "PageTwoWord" in split[2]
    assert "SecondWord" in split[2]
    assert "PageOneWord" not in split[2]


def test_ocr_page_batching_multipage_tiff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is not installed")

    monkeypatch.setenv("ARCHIV_OCR", "auto")
    monkeypatch.setenv("ARCHIV_OCR_SANDBOX", "off")
    monkeypatch.setenv("ARCHIV_OCR_LANGUAGES", "eng")

    tiff_bytes = _make_multipage_tiff(["FirstPageWord", "SecondPageWord"])
    tiff_file = tmp_path / "multipage.tiff"
    tiff_file.write_bytes(tiff_bytes)

    home = tmp_path / "home"
    res = ingest_file(tiff_file, home=home)

    layout = ArchivLayout.resolve(home)
    root = layout.derived_root(res.object_sha256)

    # Verify per-page TSVs exist
    tsv1 = root / "ocr" / "page-0001.tsv"
    tsv2 = root / "ocr" / "page-0002.tsv"
    assert tsv1.is_file()
    assert tsv2.is_file()

    # Update search index and search for text from each page
    update_search_index([res.object_sha256], home=home)
    m1 = search_documents("FirstPageWord", home=home)
    m2 = search_documents("SecondPageWord", home=home)
    assert len(m1) >= 1
    assert len(m2) >= 1
