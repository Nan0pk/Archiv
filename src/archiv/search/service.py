"""Literal full-text retrieval, exact citations, and integrity validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from archiv.contracts import Citation, CitationValidation, NormalizedDocument, SearchResult
from archiv.hashing import sha256_bytes, sha256_file
from archiv.search.index import search_index_path, segment_id_for
from archiv.search.schema import connect_index
from archiv.storage.layout import ArchivLayout


def _literal_match(query: str) -> str:
    value = query.strip()
    if not value:
        raise ValueError("query must not be empty")
    return '"' + value.replace('"', '""') + '"'


def search_documents(
    query: str,
    *,
    home: Path | None = None,
    source_name: str | None = None,
    media_type: str | None = None,
    kind: str | None = None,
    object_sha256: str | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    """Search literal text and return only independently validated citations."""

    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    layout = ArchivLayout.resolve(home)
    path = search_index_path(layout)
    if not path.is_file():
        raise FileNotFoundError("search index missing; run rebuild-search-index")

    clauses = ["segments_fts MATCH ?"]
    parameters: list[str | int] = [_literal_match(query)]
    for column, value in (
        ("source_name", source_name),
        ("media_type", media_type),
        ("kind", kind),
        ("object_sha256", object_sha256),
    ):
        if value is not None:
            clauses.append(f"s.{column} = ?")
            parameters.append(value)
    parameters.append(limit)

    statement = f"""
        SELECT s.*, bm25(segments_fts) AS rank
        FROM segments_fts
        JOIN segments AS s ON s.rowid = segments_fts.rowid
        WHERE {" AND ".join(clauses)}
        ORDER BY rank, s.segment_id
        LIMIT ?
    """
    with connect_index(path) as connection:
        rows = connection.execute(statement, parameters).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        locator = cast(
            dict[str, object],
            json.loads(str(row["locator_json"])),
        )
        citation = Citation(
            segment_id=str(row["segment_id"]),
            segment_index=int(row["segment_index"]),
            object_sha256=str(row["object_sha256"]),
            source_name=str(row["source_name"]),
            media_type=str(row["media_type"]),
            kind=str(row["kind"]),
            locator=locator,
            normalized_path=str(row["normalized_path"]),
            normalized_sha256=str(row["normalized_sha256"]),
            text_sha256=str(row["text_sha256"]),
        )
        validation = validate_citation(citation, home=home)
        if not validation.valid:
            raise RuntimeError(
                "search index contains an invalid citation: " + "; ".join(validation.errors)
            )
        results.append(
            SearchResult(
                text=str(row["text"]),
                rank=float(row["rank"]),
                citation=citation,
            )
        )
    return results


def validate_citation(
    citation: Citation,
    *,
    home: Path | None = None,
) -> CitationValidation:
    """Verify a citation against canonical bytes and normalized segment evidence."""

    layout = ArchivLayout.resolve(home)
    errors: list[str] = []
    original = layout.original_path(citation.object_sha256)
    expected_normalized = (
        layout.derived_root(citation.object_sha256) / "normalized" / "document.json"
    )

    if not original.is_file():
        errors.append("canonical original is missing")
    elif sha256_file(original) != citation.object_sha256:
        errors.append("canonical original hash mismatch")

    if Path(citation.normalized_path).resolve() != expected_normalized.resolve():
        errors.append("normalized path is not canonical for the object")
    if not expected_normalized.is_file():
        errors.append("normalized document is missing")
        return CitationValidation(valid=False, errors=errors)
    if sha256_file(expected_normalized) != citation.normalized_sha256:
        errors.append("normalized document hash mismatch")
        return CitationValidation(valid=False, errors=errors)

    document = NormalizedDocument.model_validate_json(
        expected_normalized.read_text(encoding="utf-8")
    )
    if document.object_sha256 != citation.object_sha256:
        errors.append("normalized object digest mismatch")
    if document.source_name != citation.source_name:
        errors.append("source name mismatch")
    if document.media_type != citation.media_type:
        errors.append("media type mismatch")
    if document.kind != citation.kind:
        errors.append("kind mismatch")
    if citation.segment_index >= len(document.segments):
        errors.append("segment index is out of range")
        return CitationValidation(valid=False, errors=errors)

    segment = document.segments[citation.segment_index]
    locator_json = json.dumps(
        segment.locator,
        sort_keys=True,
        separators=(",", ":"),
    )
    text_sha256 = sha256_bytes(segment.text.encode("utf-8"))
    expected_segment_id = segment_id_for(
        citation.object_sha256,
        citation.segment_index,
        locator_json,
        text_sha256,
    )
    if segment.locator != citation.locator:
        errors.append("segment locator mismatch")
    if text_sha256 != citation.text_sha256:
        errors.append("segment text hash mismatch")
    if expected_segment_id != citation.segment_id:
        errors.append("segment identifier mismatch")
    return CitationValidation(valid=not errors, errors=errors)


def read_source_excerpt(
    citation: Citation,
    *,
    home: Path | None = None,
) -> str:
    """Return exact normalized source text only after citation validation."""

    validation = validate_citation(citation, home=home)
    if not validation.valid:
        raise ValueError("invalid citation: " + "; ".join(validation.errors))
    document = NormalizedDocument.model_validate_json(
        Path(citation.normalized_path).read_text(encoding="utf-8")
    )
    return document.segments[citation.segment_index].text
