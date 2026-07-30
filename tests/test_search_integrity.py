from __future__ import annotations

from pathlib import Path

import pytest

from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index, search_documents, validate_citation
from archiv.search.index import search_index_path
from archiv.storage.layout import ArchivLayout


def test_deleted_index_rebuilds_with_stable_segment_ids(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    ingest_file(ingestion_corpus / "plain-text.txt", home=home)
    ingest_file(ingestion_corpus / "document.docx", home=home)
    rebuild_search_index(home=home)
    before = [
        result.citation.segment_id
        for result in search_documents("ARCHIV", home=home)
    ]

    path = search_index_path(ArchivLayout.resolve(home))
    path.unlink()
    assert not path.exists()
    rebuild_search_index(home=home)
    after = [
        result.citation.segment_id
        for result in search_documents("ARCHIV", home=home)
    ]

    assert before == after


def test_stale_and_nonexistent_citations_are_rejected(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    ingest_file(ingestion_corpus / "plain-text.txt", home=home)
    rebuild_search_index(home=home)
    result = search_documents("ARCHIV-TEXT-MARKER-2026", home=home)[0]

    nonexistent = result.citation.model_copy(
        update={"object_sha256": "0" * 64}
    )
    invalid = validate_citation(nonexistent, home=home)
    assert invalid.valid is False
    assert "canonical original is missing" in invalid.errors

    normalized = Path(result.citation.normalized_path)
    normalized.write_text(
        normalized.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    stale = validate_citation(result.citation, home=home)
    assert stale.valid is False
    assert "normalized document hash mismatch" in stale.errors
    with pytest.raises(RuntimeError, match="invalid citation"):
        search_documents("ARCHIV-TEXT-MARKER-2026", home=home)


def test_search_requires_an_explicitly_built_index(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    ingest_file(ingestion_corpus / "plain-text.txt", home=home)
    with pytest.raises(FileNotFoundError, match="rebuild-search-index"):
        search_documents("ARCHIV", home=home)
