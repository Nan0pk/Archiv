from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import IngestionResult
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file
from archiv.search import rebuild_search_index, search_documents
from archiv.source_location import (
    load_citation_file,
    resolve_citation_location,
    resolve_object_location,
)
from archiv.storage.layout import ArchivLayout

RUNNER = CliRunner()
MARKER = "ARCHIV-TEXT-MARKER-2026"


def _ingested_text(
    ingestion_corpus: Path, tmp_path: Path
) -> tuple[Path, IngestionResult]:
    home = tmp_path / "home"
    result = ingest_file(ingestion_corpus / "plain-text.txt", home=home)
    rebuild_search_index(home=home)
    return home, result


def test_object_location_is_bounded_hash_validated_and_read_only(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, ingestion = _ingested_text(ingestion_corpus, tmp_path)
    original = Path(ingestion.original_path)
    before = sha256_file(original)

    location = resolve_object_location(ingestion.object_sha256, home=home)

    assert location.reference_type == "object"
    assert location.object_sha256 == ingestion.object_sha256
    assert location.source_name == "plain-text.txt"
    assert location.media_type == "text/plain"
    assert location.kind == "txt"
    assert location.locator is None
    assert Path(location.canonical_path) == original.resolve()
    assert location.canonical_relative_path.startswith("originals/sha256/")
    assert location.original_hash_validated is True
    assert location.citation_validated is False
    assert location.read_only is True
    assert sha256_file(original) == before


def test_find_ask_and_report_envelopes_select_the_same_validated_citation(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, _ = _ingested_text(ingestion_corpus, tmp_path)
    citation = search_documents(MARKER, home=home)[0].citation

    payloads: list[object] = [
        [{"text": "fixture", "rank": -1.0, "citation": citation.model_dump(mode="json")}],
        {"retrieved_citations": [citation.model_dump(mode="json")]},
        {
            "sources": [
                {
                    "number": 1,
                    "citation": citation.model_dump(mode="json"),
                    "excerpt": "fixture",
                    "locator_text": "line 3",
                }
            ]
        },
    ]

    for index, payload in enumerate(payloads):
        path = tmp_path / f"citation-envelope-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = load_citation_file(path)
        assert loaded == citation
        location = resolve_citation_location(loaded, home=home)
        assert location.reference_type == "citation"
        assert location.object_sha256 == citation.object_sha256
        assert location.source_name == citation.source_name
        assert location.locator == citation.locator
        assert location.citation_validated is True
        assert location.original_hash_validated is True


def test_citation_number_is_explicit_and_bounded(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, _ = _ingested_text(ingestion_corpus, tmp_path)
    citation = search_documents(MARKER, home=home)[0].citation
    path = tmp_path / "citations.json"
    path.write_text(
        json.dumps(
            [
                {"citation": citation.model_dump(mode="json")},
                {"citation": citation.model_dump(mode="json")},
            ]
        ),
        encoding="utf-8",
    )

    assert load_citation_file(path, citation_number=2) == citation
    with pytest.raises(ValueError, match="out of range"):
        load_citation_file(path, citation_number=3)
    with pytest.raises(ValueError, match="at least 1"):
        load_citation_file(path, citation_number=0)


def test_stale_or_fabricated_citation_fails_without_a_path(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, _ = _ingested_text(ingestion_corpus, tmp_path)
    citation = search_documents(MARKER, home=home)[0].citation
    fabricated = citation.model_copy(update={"locator": {"line": 999}})

    with pytest.raises(ValueError, match="invalid citation") as caught:
        resolve_citation_location(fabricated, home=home)
    assert str(ArchivLayout.resolve(home).originals) not in str(caught.value)


def test_traversal_uppercase_and_object_substitution_fail_closed(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, ingestion = _ingested_text(ingestion_corpus, tmp_path)
    with pytest.raises(ValueError, match="lowercase 64-character"):
        resolve_object_location("../" + ingestion.object_sha256, home=home)
    with pytest.raises(ValueError, match="lowercase 64-character"):
        resolve_object_location(ingestion.object_sha256.upper(), home=home)

    wrong_digest = "0" * 64
    layout = ArchivLayout.resolve(home)
    substituted = layout.original_path(wrong_digest)
    substituted.parent.mkdir(parents=True, exist_ok=True)
    substituted.write_bytes(Path(ingestion.original_path).read_bytes())
    with pytest.raises(ValueError, match="hash mismatch"):
        resolve_object_location(wrong_digest, home=home)


def test_symlink_escape_is_rejected_even_when_target_hash_matches(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, ingestion = _ingested_text(ingestion_corpus, tmp_path)
    canonical = Path(ingestion.original_path)
    external = tmp_path / "outside-original.txt"
    external.write_bytes(canonical.read_bytes())
    canonical.unlink()
    canonical.symlink_to(external)

    with pytest.raises(ValueError, match="symbolic link"):
        resolve_object_location(ingestion.object_sha256, home=home)


def test_source_cli_human_and_json_surfaces(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, ingestion = _ingested_text(ingestion_corpus, tmp_path)
    result = search_documents(MARKER, home=home)[0]
    citation_file = tmp_path / "find-results.json"
    citation_file.write_text(
        json.dumps([result.model_dump(mode="json")]),
        encoding="utf-8",
    )

    human = RUNNER.invoke(
        app,
        ["source", "--citation-file", str(citation_file), "--home", str(home)],
    )
    assert human.exit_code == 0, human.output
    assert "Source: plain-text.txt" in human.output
    assert "Location: line 3" in human.output
    assert "Validated: immutable original and citation" in human.output
    assert "Mode: read-only" in human.output

    machine = RUNNER.invoke(
        app,
        ["source", ingestion.object_sha256, "--home", str(home), "--json"],
    )
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.output)
    assert payload["schema_version"] == "1"
    assert payload["reference_type"] == "object"
    assert payload["object_sha256"] == ingestion.object_sha256
    assert payload["read_only"] is True


def test_source_cli_requires_exactly_one_reference(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home, ingestion = _ingested_text(ingestion_corpus, tmp_path)
    citation = search_documents(MARKER, home=home)[0].citation
    citation_file = tmp_path / "citation.json"
    citation_file.write_text(citation.model_dump_json(), encoding="utf-8")

    missing = RUNNER.invoke(app, ["source", "--home", str(home)])
    assert missing.exit_code != 0
    assert "provide exactly one" in missing.output

    both = RUNNER.invoke(
        app,
        [
            "source",
            ingestion.object_sha256,
            "--citation-file",
            str(citation_file),
            "--home",
            str(home),
        ],
    )
    assert both.exit_code != 0
    assert "provide exactly one" in both.output
