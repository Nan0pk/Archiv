"""Rebuild the entity graph when, and only when, what it is built from has changed."""

from __future__ import annotations

from pathlib import Path

from archiv.graph.builder import rebuild_graph
from archiv.graph.fingerprint import graph_inputs_fingerprint
from archiv.graph.storage import FINGERPRINT_KEY, connect_graph_index, graph_index_path
from archiv.storage.layout import ArchivLayout


def ensure_graph_current(home: Path | None = None) -> bool:
    """Make sure the stored graph matches the current documents; return True if rebuilt.

    Called once at the start of each question put to the graph, so the graph is never
    answered from out of date. `archiv add` does not rebuild it any more: that re-read every
    document on every add, which took more than half of each batch and held the whole
    collection's graph in memory at once.
    """
    layout = ArchivLayout.resolve(home)
    current = graph_inputs_fingerprint(layout)
    path = graph_index_path(layout)
    if path.is_file():
        with connect_graph_index(path) as connection:
            row = connection.execute(
                "SELECT value FROM graph_meta WHERE key = ?", (FINGERPRINT_KEY,)
            ).fetchone()
        if row is not None and str(row["value"]) == current:
            return False
    rebuild_graph(home=home)
    return True
