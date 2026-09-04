"""Comprehensive tests for PR 108 Milestone 3 (Archive recursion and containment).

Tests verify:
- Children are first-class content-addressed objects with dedup across loose/archive forms.
- Containment table schema 4 provenance and query helpers.
- Safe recursive archive extraction (ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ).
- Encrypted archive detection (archive_locked = True, reason = 'encrypted').
- Hostile archive rejection (path traversal, symlinks).
- Recursion depth and aggregate byte expansion bounds.
- Grounded search and citation validation for child objects.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from archiv.contracts import IngestionStatus
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.ingestion.limits import (
    MAX_RECURSION_DEPTH,
    LimitExceededError,
)
from archiv.search import rebuild_search_index, search_documents
from archiv.search.service import validate_citation
from archiv.source_location import resolve_citation_location
from archiv.storage.database import (
    ArchivDatabase,
    get_containment_for_child,
    get_containment_for_parent,
)
from archiv.storage.layout import ArchivLayout


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def _make_tar(files: dict[str, bytes], mode: str = "w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=cast(Any, mode)) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 1700000000
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_encrypted_zip(filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(filename, content)
    raw = bytearray(buf.getvalue())
    # Set bit 0 (encrypted) in central directory and local headers
    cd_idx = raw.find(b"PK\x01\x02")
    if cd_idx >= 0:
        raw[cd_idx + 8] |= 0x01
    lf_idx = raw.find(b"PK\x03\x04")
    if lf_idx >= 0:
        raw[lf_idx + 6] |= 0x01
    return bytes(raw)


def test_loose_vs_archive_deduplication(tmp_path: Path) -> None:
    home = tmp_path / "home"
    layout = ArchivLayout.resolve(home)

    # 1. Ingest loose file
    loose_file = tmp_path / "shared.txt"
    shared_content = b"Content shared loose and inside archive.\n"
    loose_file.write_bytes(shared_content)
    loose_result = ingest_file(loose_file, home=home)
    assert loose_result.status == IngestionStatus.SUCCEEDED
    assert not loose_result.duplicate
    loose_digest = loose_result.object_sha256

    # 2. Ingest archive containing the exact same shared file plus another file
    archive_bytes = _make_zip(
        {
            "shared.txt": shared_content,
            "unique.txt": b"Unique member content.\n",
        }
    )
    archive_file = tmp_path / "bundle.zip"
    archive_file.write_bytes(archive_bytes)
    archive_result = ingest_file(archive_file, home=home)
    assert archive_result.status == IngestionStatus.SUCCEEDED

    database = ArchivDatabase(layout.database)

    # 3. Check containment provenance
    parent_records = get_containment_for_parent(database, archive_result.object_sha256)
    assert len(parent_records) == 2
    paths = {rec["internal_path"]: rec for rec in parent_records}
    assert "shared.txt" in paths
    assert "unique.txt" in paths

    # The child_sha256 for shared.txt must match loose_digest exactly
    assert paths["shared.txt"]["child_sha256"] == loose_digest
    assert paths["shared.txt"]["depth"] == 1

    # Check child containment helper
    child_records = get_containment_for_child(database, loose_digest)
    assert len(child_records) == 1
    assert child_records[0]["parent_sha256"] == archive_result.object_sha256

    # 4. Verify dedup: objects table has only 1 row for loose_digest
    with database.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM objects WHERE sha256 = ?", (loose_digest,)
        ).fetchone()[0]
        assert count == 1

        # Physical original exists once
        original_path = layout.original_path(loose_digest)
        assert original_path.is_file()
        assert sha256_file(original_path) == loose_digest


def test_nested_archive_recursion(tmp_path: Path) -> None:
    home = tmp_path / "home"
    layout = ArchivLayout.resolve(home)

    leaf_content = b"Deep nested leaf text document.\n"
    inner_tar_gz = _make_tar({"leaf.txt": leaf_content}, mode="w:gz")
    outer_zip = _make_zip({"inner.tar.gz": inner_tar_gz})

    outer_file = tmp_path / "nested_outer.zip"
    outer_file.write_bytes(outer_zip)

    result = ingest_file(outer_file, home=home)
    assert result.status == IngestionStatus.SUCCEEDED
    outer_digest = result.object_sha256

    database = ArchivDatabase(layout.database)

    # outer -> inner.tar.gz at depth 1
    outer_children = get_containment_for_parent(database, outer_digest)
    assert len(outer_children) == 1
    inner_digest = str(outer_children[0]["child_sha256"])
    assert outer_children[0]["internal_path"] == "inner.tar.gz"
    assert outer_children[0]["depth"] == 1

    # inner.tar.gz -> leaf.txt at depth 2
    inner_children = get_containment_for_parent(database, inner_digest)
    assert len(inner_children) == 1
    leaf_digest = str(inner_children[0]["child_sha256"])
    assert inner_children[0]["internal_path"] == "leaf.txt"
    assert inner_children[0]["depth"] == 2

    # Verify leaf object exists in originals
    leaf_original = layout.original_path(leaf_digest)
    assert leaf_original.is_file()
    assert leaf_original.read_bytes() == leaf_content


def test_encrypted_archive_flagged_locked_and_not_extracted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    layout = ArchivLayout.resolve(home)

    enc_zip_bytes = _make_encrypted_zip("secret.txt", b"top secret data")
    enc_file = tmp_path / "encrypted.zip"
    enc_file.write_bytes(enc_zip_bytes)

    result = ingest_file(enc_file, home=home)
    assert result.status == IngestionStatus.SUCCEEDED
    digest = result.object_sha256

    database = ArchivDatabase(layout.database)

    # Containment must have 0 rows (members not extracted)
    children = get_containment_for_parent(database, digest)
    assert len(children) == 0

    # Processing evidence recorded with status skipped and locked reason
    with database.connect() as conn:
        run = conn.execute(
            """
            SELECT status, error FROM processing_runs
            WHERE object_sha256 = ? AND processor = 'archiv.archive-extract'
            """,
            (digest,),
        ).fetchone()
        assert run is not None
        assert run["status"] == "skipped"
        assert "archive locked: encrypted" in str(run["error"])


def test_hostile_archive_traversal_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"

    # ZIP traversal
    evil_zip_bytes = _make_zip({"../evil.txt": b"hostile"})
    evil_zip = tmp_path / "evil.zip"
    evil_zip.write_bytes(evil_zip_bytes)

    with pytest.raises(LimitExceededError, match="unsafe member path"):
        ingest_file(evil_zip, home=home)

    # TAR traversal
    evil_tar_bytes = _make_tar({"../evil.txt": b"hostile"})
    evil_tar = tmp_path / "evil.tar"
    evil_tar.write_bytes(evil_tar_bytes)

    with pytest.raises(LimitExceededError, match="unsafe member path"):
        ingest_file(evil_tar, home=home)


def test_hostile_archive_symlink_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        info = tarfile.TarInfo(name="evil_symlink")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)

    symlink_tar = tmp_path / "symlink.tar"
    symlink_tar.write_bytes(buf.getvalue())

    with pytest.raises(LimitExceededError, match="archive symbolic link is not allowed"):
        ingest_file(symlink_tar, home=home)


def test_recursion_depth_limit_exceeded(tmp_path: Path) -> None:
    home = tmp_path / "home"

    # Construct nested zip chain deeper than MAX_RECURSION_DEPTH (8)
    current = _make_zip({"leaf.txt": b"too deep"})
    for i in range(MAX_RECURSION_DEPTH + 1, 0, -1):
        current = _make_zip({f"level_{i}.zip": current})

    deep_zip = tmp_path / "deep.zip"
    deep_zip.write_bytes(current)

    with pytest.raises(LimitExceededError, match="archive recursion depth limit exceeded"):
        ingest_file(deep_zip, home=home)


def test_child_search_grounding_and_citation(tmp_path: Path) -> None:
    home = tmp_path / "home"

    secret_key = "ARCHIV_RECURSIVE_TEST_PASSAGE_2026"
    archive_bytes = _make_zip(
        {
            "memo.txt": f"Important memo with verified claim: {secret_key}.\n".encode(),
            "ignored.txt": b"Other unrelated text.\n",
        }
    )
    archive_file = tmp_path / "citable_bundle.zip"
    archive_file.write_bytes(archive_bytes)

    ingest_file(archive_file, home=home)
    build = rebuild_search_index(home=home)
    assert build.object_count >= 2  # archive + at least memo.txt

    # Search for secret key
    matches = search_documents(secret_key, home=home)
    assert len(matches) == 1
    citation = matches[0].citation
    assert citation.source_name == "memo.txt"

    # Citation validation passes
    validation = validate_citation(citation, home=home)
    assert validation.valid

    # Source location resolves canonical original of child
    location = resolve_citation_location(citation, home=home)
    assert location.original_hash_validated
    assert location.citation_validated
    assert Path(location.canonical_path).is_file()
    assert secret_key.encode() in Path(location.canonical_path).read_bytes()


def test_tar_format_variants_ingestion(tmp_path: Path) -> None:
    home = tmp_path / "home"

    variants = [
        ("test.tar", "w"),
        ("test.tar.gz", "w:gz"),
        ("test.tar.bz2", "w:bz2"),
        ("test.tar.xz", "w:xz"),
    ]

    for filename, mode in variants:
        archive_path = tmp_path / filename
        archive_path.write_bytes(_make_tar({"sample.txt": b"tar variant test\n"}, mode=mode))

        result = ingest_file(archive_path, home=home)
        assert result.status == IngestionStatus.SUCCEEDED

        database = ArchivDatabase(ArchivLayout.resolve(home).database)
        records = get_containment_for_parent(database, result.object_sha256)
        assert len(records) == 1
        assert records[0]["internal_path"] == "sample.txt"
        assert records[0]["compression"] == "tar"
        assert records[0]["depth"] == 1


def test_unsupported_members_in_archive_gracefully_skipped(tmp_path: Path) -> None:
    home = tmp_path / "home"
    database = ArchivDatabase(ArchivLayout.resolve(home).database)

    archive_bytes = _make_zip(
        {
            "valid.txt": b"Supported text file.\n",
            "unsupported.xyz": b"Unsupported binary or format.\n",
        }
    )
    archive_file = tmp_path / "mixed.zip"
    archive_file.write_bytes(archive_bytes)

    result = ingest_file(archive_file, home=home)
    assert result.status == IngestionStatus.SUCCEEDED

    # valid.txt is recorded in containment, unsupported.xyz is skipped without error
    records = get_containment_for_parent(database, result.object_sha256)
    assert len(records) == 1
    assert records[0]["internal_path"] == "valid.txt"


def test_aggregate_expansion_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"

    monkeypatch.setattr("archiv.ingestion.service.MAX_EXPANDED_BYTES", 500)
    monkeypatch.setattr("archiv.ingestion.limits.MAX_EXPANDED_BYTES", 10_000)

    inner = _make_zip({"inner.txt": b"B" * 400})
    outer = _make_zip({"outer.txt": b"A" * 400, "nested.zip": inner})

    bomb = tmp_path / "bomb.zip"
    bomb.write_bytes(outer)

    with pytest.raises(LimitExceededError, match="archive expanded bytes limit exceeded"):
        ingest_file(bomb, home=home)
