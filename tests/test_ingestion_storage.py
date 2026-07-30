from __future__ import annotations

import json
import shutil
import sqlite3
import stat
from pathlib import Path
from typing import cast

from ingestion_support import VALID_INGESTION_FIXTURES

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file, rebuild_derived
from archiv.storage.layout import ArchivLayout


def _count(database: Path, table: str) -> int:
    with sqlite3.connect(database) as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return cast(int, row[0])


def test_ingests_every_representative_valid_format(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    for filename in VALID_INGESTION_FIXTURES:
        source = ingestion_corpus / filename
        source_before = source.read_bytes()
        result = ingest_file(source, home=home)

        assert result.status == "succeeded"
        assert result.source_hash_unchanged is True
        assert source.read_bytes() == source_before
        original = Path(result.original_path)
        assert original.read_bytes() == source_before
        assert stat.S_IMODE(original.stat().st_mode) == 0o444
        normalized = Path(result.derived_root) / "normalized" / "document.json"
        payload = json.loads(normalized.read_text(encoding="utf-8"))
        assert payload["object_sha256"] == result.object_sha256
        assert payload["source_name"] == filename

    layout = ArchivLayout.resolve(home)
    assert _count(layout.database, "objects") == len(VALID_INGESTION_FIXTURES)
    assert _count(layout.database, "ingestions") == len(VALID_INGESTION_FIXTURES)


def test_duplicate_ingestion_reuses_one_original(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    source = ingestion_corpus / "document.docx"
    first = ingest_file(source, home=home)
    normalized = Path(first.derived_root) / "normalized" / "document.json"
    normalized_hash = sha256_file(normalized)
    renamed = tmp_path / "renamed-copy.docx"
    renamed.write_bytes(source.read_bytes())
    second = ingest_file(renamed, home=home)

    assert first.object_sha256 == second.object_sha256
    assert first.duplicate is False
    assert second.duplicate is True
    assert first.original_path == second.original_path
    assert sha256_file(normalized) == normalized_hash
    assert second.processing[0].output_kind == "derived-existing"
    payload = json.loads(normalized.read_text(encoding="utf-8"))
    assert payload["source_name"] == "document.docx"

    layout = ArchivLayout.resolve(home)
    assert _count(layout.database, "objects") == 1
    assert _count(layout.database, "ingestions") == 2


def test_derived_data_can_be_deleted_and_rebuilt(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    result = ingest_file(ingestion_corpus / "workbook.xlsx", home=home)
    original = Path(result.original_path)
    original_hash = sha256_file(original)
    derived = Path(result.derived_root)
    normalized_before = sha256_file(derived / "normalized" / "document.json")

    shutil.rmtree(derived)
    assert original.is_file()
    evidence = rebuild_derived(result.object_sha256, home=home)

    assert evidence
    assert sha256_file(original) == original_hash
    assert sha256_file(derived / "normalized" / "document.json") == normalized_before
    assert (derived / "tables" / "tables.json").is_file()
