"""Rebuildable full-text retrieval and citation services."""

from archiv.search.index import rebuild_search_index
from archiv.search.service import read_source_excerpt, search_documents, validate_citation

__all__ = [
    "read_source_excerpt",
    "rebuild_search_index",
    "search_documents",
    "validate_citation",
]
