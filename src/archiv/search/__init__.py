"""Rebuildable full-text retrieval and citation services."""

from archiv.search.index import rebuild_search_index
from archiv.search.retrieval import (
    derive_query_variants,
    retrieve_evidence,
    sanitized_retrieval_diagnostics,
)
from archiv.search.service import read_source_excerpt, search_documents, validate_citation

__all__ = [
    "derive_query_variants",
    "read_source_excerpt",
    "rebuild_search_index",
    "retrieve_evidence",
    "sanitized_retrieval_diagnostics",
    "search_documents",
    "validate_citation",
]
