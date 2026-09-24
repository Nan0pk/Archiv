"""Perceptual image embeddings, an index over them, and near-duplicate detection."""

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
    count_image_objects,
    image_index_path,
    image_object_digests,
    pack_embedding,
    rebuild_image_index,
    unpack_embedding,
    update_image_index,
)
from archiv.images.search import find_near_duplicates, find_similar_images

__all__ = [
    "ImageEmbedder",
    "ImageIndexBuildResult",
    "ImageSearchResult",
    "NearDuplicateGroup",
    "NearDuplicateItem",
    "PerceptualFeatureEmbedder",
    "connect_image_index",
    "count_image_objects",
    "find_near_duplicates",
    "get_default_image_embedder",
    "image_index_path",
    "image_object_digests",
    "normalize_vector",
    "pack_embedding",
    "rebuild_image_index",
    "find_similar_images",
    "unpack_embedding",
    "update_image_index",
]
