"""Evidence-backed entity graph and cross-corpus relationship querying."""

from archiv.graph.builder import rebuild_graph
from archiv.graph.contracts import (
    CrossCorpusQueryResult,
    EntityAppearance,
    EntityMention,
    EntityProfile,
    GraphCitation,
    GraphEdge,
    GraphNode,
)
from archiv.graph.queries import get_entity_profile, query_cross_corpus
from archiv.graph.storage import (
    connect_graph_index,
    find_node_by_name,
    get_graph_stats,
    get_node,
    graph_index_path,
)

__all__ = [
    "CrossCorpusQueryResult",
    "EntityAppearance",
    "EntityMention",
    "EntityProfile",
    "GraphCitation",
    "GraphEdge",
    "GraphNode",
    "connect_graph_index",
    "find_node_by_name",
    "get_entity_profile",
    "get_graph_stats",
    "get_node",
    "graph_index_path",
    "query_cross_corpus",
    "rebuild_graph",
]
