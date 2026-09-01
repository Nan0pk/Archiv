"""Deterministic local query derivation and explainable evidence selection."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from archiv.contracts import (
    Citation,
    EvidenceRetrieval,
    RetrievalDiagnostics,
    RetrievalQueryVariant,
    RetrievalSelection,
    SearchResult,
)
from archiv.search.index import search_index_path
from archiv.search.schema import connect_index
from archiv.search.service import search_documents, validate_citation
from archiv.storage.layout import ArchivLayout

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"[^\W_]+(?:['’.-][^\W_]+)*|\d+(?:[.,:/-]\d+)*",
    re.UNICODE,
)
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "do",
        "does",
        "for",
        "from",
        "have",
        "has",
        "how",
        "in",
        "into",
        "is",
        "it",
        "made",
        "of",
        "on",
        "or",
        "prepare",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)
_GENERIC_TERMS: Final[frozenset[str]] = frozenset(
    {
        "claim",
        "claims",
        "current",
        "described",
        "details",
        "document",
        "documents",
        "established",
        "evidence",
        "information",
        "missing",
        "policy",
        "project",
        "source",
        "sources",
        "status",
        "work",
    }
)


@dataclass(frozen=True)
class _Concept:
    triggers: frozenset[str]
    queries: tuple[str, ...]


_CONCEPTS: Final[dict[str, _Concept]] = {
    "architecture": _Concept(
        triggers=frozenset({"architecture", "architectural"}),
        queries=("architecture", "approved architecture", "search backend", "content-addressed"),
    ),
    "decisions": _Concept(
        triggers=frozenset({"decision", "decisions", "decided"}),
        queries=("decision", "decided", "approved"),
    ),
    "status": _Concept(
        triggers=frozenset({"overview", "status", "summary"}),
        queries=("project status", "approved and current"),
    ),
    "completed-work": _Concept(
        triggers=frozenset({"complete", "completed", "done", "finished"}),
        queries=("completed work", "completed", "implemented", "delivered"),
    ),
    "unfinished-work": _Concept(
        triggers=frozenset(
            {"incomplete", "outstanding", "pending", "remains", "unfinished", "unresolved"}
        ),
        queries=("unfinished work", "unresolved", "remains unresolved", "not yet"),
    ),
    "risks": _Concept(
        triggers=frozenset({"mitigation", "risk", "risks"}),
        queries=("risk", "risks", "mitigation", "likelihood", "impact"),
    ),
    "contradictions": _Concept(
        triggers=frozenset(
            {"conflict", "conflicting", "conflicts", "contradiction", "contradictions"}
        ),
        queries=("requires", "not approved", "superseded", "according to"),
    ),
    "dates-and-deadlines": _Concept(
        triggers=frozenset(
            {"date", "dates", "deadline", "deadlines", "due", "schedule", "scheduled"}
        ),
        queries=(
            "date",
            "deadline",
            "due",
            "approval date",
            "review date",
            "start date",
            "approved on",
        ),
    ),
    "numerical-claims": _Concept(
        triggers=frozenset({"number", "numbers", "numeric", "numerical", "quantitative"}),
        queries=("budget", "target", "tests", "files", "likelihood", "impact"),
    ),
}


@dataclass(frozen=True)
class _QuerySpec:
    kind: str
    query: str
    weight: int


@dataclass
class _SourceCandidate:
    score: float = 0.0
    best_result: SearchResult | None = None
    best_result_score: float = float("-inf")
    matched_queries: set[str] = field(default_factory=lambda: set[str]())


def _tokens(objective: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(objective)]


def _append_variant(
    variants: list[_QuerySpec],
    seen: set[str],
    *,
    kind: str,
    query: str,
    weight: int,
) -> None:
    compact = " ".join(query.split()).strip()
    key = compact.casefold()
    if not compact or key in seen:
        return
    variants.append(_QuerySpec(kind=kind, query=compact, weight=weight))
    seen.add(key)


def derive_query_variants(objective: str) -> tuple[list[_QuerySpec], list[str], list[str]]:
    """Derive a stable bounded set of literal searches from one objective."""

    value = objective.strip()
    if not value:
        raise ValueError("query cannot be empty")

    tokens = _tokens(value)
    variants: list[_QuerySpec] = []
    seen: set[str] = set()
    _append_variant(variants, seen, kind="exact-objective", query=value, weight=120)

    phrase_tokens = [token for token in tokens if token not in _STOPWORDS]
    phrase_count = 0
    for size in (3, 2):
        for index in range(0, max(0, len(phrase_tokens) - size + 1)):
            phrase = phrase_tokens[index : index + size]
            if all(token in _GENERIC_TERMS for token in phrase):
                continue
            _append_variant(
                variants,
                seen,
                kind=f"derived-{size}-term-phrase",
                query=" ".join(phrase),
                weight=80,
            )
            phrase_count += 1
            if phrase_count >= 6:
                break
        if phrase_count >= 6:
            break

    derived_terms: list[str] = []
    for token in tokens:
        if token in _STOPWORDS or token in _GENERIC_TERMS or len(token) < 2:
            continue
        if token not in derived_terms:
            derived_terms.append(token)
        if len(derived_terms) == 8:
            break
    for term in derived_terms:
        _append_variant(variants, seen, kind="derived-term", query=term, weight=60)

    token_set = set(tokens)
    triggered_concepts: list[str] = []
    for name, concept in _CONCEPTS.items():
        if token_set.isdisjoint(concept.triggers):
            continue
        triggered_concepts.append(name)
        for query in concept.queries:
            _append_variant(
                variants,
                seen,
                kind=f"concept:{name}",
                query=query,
                weight=90,
            )

    return variants[:24], derived_terms, triggered_concepts


_SPREADSHEET_KINDS: Final[frozenset[str]] = frozenset({"xlsx", "xls", "ods", "ots", "fods"})
_A1_CELL_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z]+)(\d+)$")
_ROW_AWARE_PHRASE_KIND: Final[str] = "row-aware-phrase"
_ROW_AWARE_PHRASE_WEIGHT: Final[int] = 70


def _column_index_from_letters(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _row_position(locator: dict[str, object], kind: str) -> tuple[str, int, int] | None:
    """Return (sheet, row_number, column_index) for a spreadsheet cell, else None.

    Handles both locator shapes this project's spreadsheet normalizers emit:
    xlsx/xls use A1 notation (``{"sheet", "cell"}``); ods/ots/fods use numeric
    coordinates (``{"sheet", "row", "column"}``, sometimes plus ``"formula"``).
    """

    if kind not in _SPREADSHEET_KINDS:
        return None
    sheet = locator.get("sheet")
    if not isinstance(sheet, str):
        return None
    cell = locator.get("cell")
    if isinstance(cell, str):
        match = _A1_CELL_RE.match(cell)
        if not match:
            return None
        return sheet, int(match.group(2)), _column_index_from_letters(match.group(1))
    row, column = locator.get("row"), locator.get("column")
    if isinstance(row, int) and isinstance(column, int):
        return sheet, row, column
    return None


def _document_spreadsheet_segments(
    object_sha256: str, kind: str, *, home: Path | None
) -> list[sqlite3.Row]:
    layout = ArchivLayout.resolve(home)
    path = search_index_path(layout)
    with connect_index(path) as connection:
        return connection.execute(
            "SELECT * FROM segments WHERE object_sha256 = ? AND kind = ?",
            (object_sha256, kind),
        ).fetchall()


def _row_aware_phrase_matches(
    phrase: str,
    *,
    home: Path | None,
    limit: int,
) -> list[SearchResult]:
    """Find a phrase that spans adjacent spreadsheet cells within one row.

    ``search_documents`` (used by ``archiv find``) indexes each normalized
    cell independently and is never modified here. This looks only at
    documents an ordinary single-word search already surfaced, reconstructs
    each candidate row from the segments already in the index (never from
    ``NormalizedTable.rows``, whose blank-row skipping breaks any relationship
    to a segment), and cites the individual cell within a matching row with
    the strongest word overlap -- never a cell whose own text does not
    contain what it is cited for.
    """

    words = [word for word in phrase.casefold().split() if word]
    if len(words) < 2:
        return []

    candidate_documents: dict[tuple[str, str], None] = {}
    for word in words:
        for result in search_documents(word, home=home, limit=limit):
            citation = result.citation
            if _row_position(citation.locator, citation.kind) is not None:
                candidate_documents.setdefault((citation.object_sha256, citation.kind), None)

    matches: list[SearchResult] = []
    seen_segment_ids: set[str] = set()
    for object_sha256, kind in candidate_documents:
        rows: dict[tuple[str, int], list[tuple[int, sqlite3.Row]]] = {}
        for row in _document_spreadsheet_segments(object_sha256, kind, home=home):
            locator = cast(dict[str, object], json.loads(str(row["locator_json"])))
            position = _row_position(locator, kind)
            if position is None:
                continue
            sheet, row_number, column_index = position
            rows.setdefault((sheet, row_number), []).append((column_index, row))

        for cells in rows.values():
            if len(cells) < 2:
                continue
            cells.sort(key=lambda entry: entry[0])
            row_text = " ".join(str(cell_row["text"]) for _, cell_row in cells).casefold()
            if phrase not in row_text:
                continue
            _, best_row = max(
                cells,
                key=lambda entry: (
                    sum(1 for word in words if word in str(entry[1]["text"]).casefold()),
                    -entry[0],
                ),
            )
            if str(best_row["segment_id"]) in seen_segment_ids:
                continue
            citation = Citation(
                segment_id=str(best_row["segment_id"]),
                segment_index=int(best_row["segment_index"]),
                object_sha256=str(best_row["object_sha256"]),
                source_name=str(best_row["source_name"]),
                media_type=str(best_row["media_type"]),
                kind=str(best_row["kind"]),
                locator=cast(dict[str, object], json.loads(str(best_row["locator_json"]))),
                normalized_path=str(best_row["normalized_path"]),
                normalized_sha256=str(best_row["normalized_sha256"]),
                text_sha256=str(best_row["text_sha256"]),
            )
            if not validate_citation(citation, home=home).valid:
                continue
            seen_segment_ids.add(citation.segment_id)
            matches.append(SearchResult(text=str(best_row["text"]), rank=0.0, citation=citation))
            if len(matches) >= limit:
                return matches
    return matches


def retrieve_evidence(
    objective: str,
    *,
    home: Path | None = None,
    evidence_limit: int = 8,
    per_query_limit: int = 40,
) -> EvidenceRetrieval:
    """Retrieve source-diverse validated evidence for a natural-language objective."""

    if evidence_limit < 1 or evidence_limit > 50:
        raise ValueError("evidence_limit must be between 1 and 50")
    if per_query_limit < 1 or per_query_limit > 100:
        raise ValueError("per_query_limit must be between 1 and 100")

    variants, derived_terms, triggered_concepts = derive_query_variants(objective)
    sources: dict[str, _SourceCandidate] = {}
    candidate_segments: set[str] = set()
    recorded_variants: list[RetrievalQueryVariant] = []

    query_results: list[tuple[_QuerySpec, list[SearchResult]]] = []
    for spec in variants:
        results = search_documents(spec.query, home=home, limit=per_query_limit)
        query_results.append((spec, results))
        if spec.kind.endswith("-term-phrase") and not results:
            row_aware_results = _row_aware_phrase_matches(
                spec.query, home=home, limit=per_query_limit
            )
            if row_aware_results:
                query_results.append(
                    (
                        _QuerySpec(
                            kind=_ROW_AWARE_PHRASE_KIND,
                            query=spec.query,
                            weight=_ROW_AWARE_PHRASE_WEIGHT,
                        ),
                        row_aware_results,
                    )
                )

    for spec, results in query_results:
        recorded_variants.append(
            RetrievalQueryVariant(
                kind=spec.kind,
                query=spec.query,
                weight=spec.weight,
                result_count=len(results),
            )
        )
        seen_sources_for_query: set[str] = set()
        for position, result in enumerate(results):
            citation = result.citation
            candidate_segments.add(citation.segment_id)
            source = sources.setdefault(citation.object_sha256, _SourceCandidate())
            position_bonus = 1.0 / (position + 1)
            if citation.object_sha256 not in seen_sources_for_query:
                source.score += float(spec.weight) + position_bonus
                source.matched_queries.add(spec.query)
                seen_sources_for_query.add(citation.object_sha256)

            information_bonus = min(len(" ".join(result.text.split())), 400) / 1000.0
            result_score = float(spec.weight) + position_bonus + information_bonus
            if result_score > source.best_result_score:
                source.best_result = result
                source.best_result_score = result_score

    ordered_sources = sorted(
        (source for source in sources.values() if source.best_result is not None),
        key=lambda source: (
            -source.score,
            source.best_result.citation.source_name.casefold()
            if source.best_result is not None
            else "",
            source.best_result.citation.segment_id if source.best_result is not None else "",
        ),
    )
    selected_sources = ordered_sources[:evidence_limit]
    selected_results = [
        source.best_result for source in selected_sources if source.best_result is not None
    ]
    selections = [
        RetrievalSelection(
            segment_id=result.citation.segment_id,
            object_sha256=result.citation.object_sha256,
            source_name=result.citation.source_name,
            locator=result.citation.locator,
            rank=result.rank,
            score=round(source.score, 6),
            matched_queries=sorted(source.matched_queries, key=str.casefold),
        )
        for source, result in zip(selected_sources, selected_results, strict=True)
    ]
    diagnostics = RetrievalDiagnostics(
        original_objective=objective,
        derived_terms=derived_terms,
        triggered_concepts=triggered_concepts,
        query_variants=recorded_variants,
        evidence_limit=evidence_limit,
        candidate_count=len(candidate_segments),
        selected_count=len(selected_results),
        selections=selections,
    )
    return EvidenceRetrieval(results=selected_results, diagnostics=diagnostics)


def sanitized_retrieval_diagnostics(diagnostics: RetrievalDiagnostics) -> dict[str, object]:
    """Return shareable aggregate diagnostics without user text or identifying metadata."""

    variant_counts: dict[str, int] = {}
    for variant in diagnostics.query_variants:
        variant_counts[variant.kind] = variant_counts.get(variant.kind, 0) + 1
    return {
        "schema_version": diagnostics.schema_version,
        "strategy_version": diagnostics.strategy_version,
        "objective_sha256": hashlib.sha256(
            diagnostics.original_objective.encode("utf-8")
        ).hexdigest(),
        "objective_character_count": len(diagnostics.original_objective),
        "derived_term_count": len(diagnostics.derived_terms),
        "triggered_concept_count": len(diagnostics.triggered_concepts),
        "query_variant_counts": variant_counts,
        "query_variant_count": len(diagnostics.query_variants),
        "evidence_limit": diagnostics.evidence_limit,
        "candidate_count": diagnostics.candidate_count,
        "selected_count": diagnostics.selected_count,
        "selection_scores": [selection.score for selection in diagnostics.selections],
        "selection_ranks": [selection.rank for selection in diagnostics.selections],
        "matched_query_counts": [
            len(selection.matched_queries) for selection in diagnostics.selections
        ],
    }
