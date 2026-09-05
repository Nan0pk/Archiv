"""Image embeddings, semantic image search, and near-duplicate detection."""

from archiv.images.contracts import (
    ImageIndexBuildResult,
    ImageSearchResult,
    NearDuplicateGroup,
    NearDuplicateItem,
)
from archiv.images.embedder import (
    ImageEmbedder,
    PerceptualFeatureEmbedder,
    get_default_image_embedder,
    normalize_vector,
)
from archiv.images.index import (
    connect_image_index,
    image_index_path,
    pack_embedding,
    rebuild_image_index,
    unpack_embedding,
)
from archiv.images.search import find_near_duplicates, search_images

__all__ = [
    "ImageEmbedder",
    "ImageIndexBuildResult",
    "ImageSearchResult",
    "NearDuplicateGroup",
    "NearDuplicateItem",
    "PerceptualFeatureEmbedder",
    "connect_image_index",
    "find_near_duplicates",
    "get_default_image_embedder",
    "image_index_path",
    "normalize_vector",
    "pack_embedding",
    "rebuild_image_index",
    "search_images",
    "unpack_embedding",
]
