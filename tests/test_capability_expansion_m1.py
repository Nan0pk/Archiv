"""Tests for Capability Expansion Milestone 1 deliverables.

Covers:
- Extractor registry and signature-first format detection.
- Database processing_queue operations.
- Single-pass copy and hash.
- Real WebP thumbnail generation.
- CLI queue tracking in status and draining with process command.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.hashing import copy_and_hash, sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.extractors import (
    ALL_EXTRACTORS,
    check_content_signature,
    export_registry_compatibility,
    get_extractor,
)
from archiv.ingestion.formats import SUPPORTED_SUFFIXES, MalformedInputError
from archiv.ingestion.normalizers import normalize
from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout
from archiv.storage.queue import (
    enqueue_job,
    fetch_pending_jobs,
    get_queue_depth,
    update_job,
)

runner = CliRunner()


def test_extractor_registry_covers_all_supported_suffixes() -> None:
    registry_suffixes = {suffix for extractor in ALL_EXTRACTORS for suffix in extractor.suffixes}
    assert registry_suffixes == SUPPORTED_SUFFIXES

    summary = export_registry_compatibility()
    assert len(summary) == len(ALL_EXTRACTORS)
    assert any(item["name"] == "png-image" and item["kind"] == "image" for item in summary)
    assert any(item["name"] == "plain-text" and item["kind"] == "text" for item in summary)


def test_signature_first_content_detection_rejection(tmp_path: Path) -> None:
    # A .png file containing JPEG magic bytes should be rejected
    mismatched_png = tmp_path / "fake.png"
    mismatched_png.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)

    extractor = get_extractor(".png")
    with pytest.raises(MalformedInputError, match="file content signature does not match"):
        check_content_signature(mismatched_png, extractor, ".png")

    with pytest.raises(MalformedInputError, match="file content signature does not match"):
        normalize(mismatched_png, sha256_file(mismatched_png), source_name="fake.png")

    # A .txt file containing PDF magic bytes should be rejected
    binary_txt = tmp_path / "sneaky.txt"
    binary_txt.write_bytes(b"%PDF-1.4\nSome PDF stream")

    txt_extractor = get_extractor(".txt")
    with pytest.raises(MalformedInputError, match="binary file content signature found"):
        check_content_signature(binary_txt, txt_extractor, ".txt")

    with pytest.raises(MalformedInputError, match="binary file content signature found"):
        normalize(binary_txt, sha256_file(binary_txt), source_name="sneaky.txt")


def test_copy_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    content = b"Single-pass stream copy and hash verification test" * 100
    source.write_bytes(content)

    digest = copy_and_hash(source, target)
    assert digest == sha256_file(source)
    assert target.is_file()
    assert target.read_bytes() == content


def test_processing_queue_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "queue_test.sqlite3"
    db = ArchivDatabase(db_path)
    db.initialize()

    dummy_digest = "0" * 64
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO objects (
                sha256, size, media_type, source_extension, storage_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (dummy_digest, 123, "text/plain", ".txt", "dummy/path", "2026-09-04T00:00:00Z"),
        )
        conn.commit()
    enqueue_job(db, dummy_digest, "archiv.visual-ocr", processor_version="1", state="pending")

    depth = get_queue_depth(db)
    assert depth["pending"] == 1
    assert depth["processing"] == 0

    pending = fetch_pending_jobs(db, processor="archiv.visual-ocr")
    assert len(pending) == 1
    assert pending[0]["object_sha256"] == dummy_digest
    assert pending[0]["state"] == "pending"

    update_job(db, dummy_digest, "archiv.visual-ocr", state="completed")
    depth_after = get_queue_depth(db)
    assert depth_after["pending"] == 0
    assert depth_after["completed"] == 1


def test_thumbnail_generation_and_queue_tracking(tmp_path: Path, ingestion_corpus: Path) -> None:
    home = tmp_path / "home"
    sample_png = ingestion_corpus / "scanned-page.png"
    result = ingest_file(sample_png, home=home)

    derived = Path(result.derived_root)
    thumb_path = derived / "previews" / "thumbnail.webp"
    assert thumb_path.is_file()
    data = thumb_path.read_bytes()
    assert len(data) > 0
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WEBP"

    # Verify queue depth in status CLI
    status = runner.invoke(app, ["status", "--home", str(home), "--json"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    assert "queue" in payload
    assert payload["queue"]["completed"] >= 1 or payload["queue"]["pending"] >= 0


def test_archiv_process_cli(tmp_path: Path, ingestion_corpus: Path) -> None:
    home = tmp_path / "process_home"
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    db = ArchivDatabase(layout.database)
    db.initialize()

    # Empty queue run
    empty_run = runner.invoke(app, ["process", "--home", str(home), "--json"])
    assert empty_run.exit_code == 0, empty_run.output
    empty_payload = json.loads(empty_run.output)
    assert empty_payload["status"] == "succeeded"
    assert empty_payload["processed"] == 0
    assert empty_payload["remaining"] == 0

    # Ingest a sample file, then enqueue a manual visual-ocr pending job
    sample_png = ingestion_corpus / "scanned-page.png"
    result = ingest_file(sample_png, home=home)
    enqueue_job(db, result.object_sha256, "archiv.visual-ocr", state="pending")

    pending_depth = get_queue_depth(db)
    assert pending_depth["pending"] == 1

    # Run process command to drain
    proc_run = runner.invoke(app, ["process", "--home", str(home), "--json"])
    assert proc_run.exit_code == 0, proc_run.output
    proc_payload = json.loads(proc_run.output)
    assert proc_payload["status"] == "succeeded"
    assert proc_payload["remaining"] == 0
