"""Finding images that look like a given image, over the image embedding index.

There is no text query here. The one that used to exist gave every string a vector, so
`"a photo of a person smiling"` returned a ranked table of documents and `"xyzzy plugh
frobnicate"` returned one just as confidently. `docs/plan/steps/S10.md` records what it
did, measured, on a corpus containing no people.

What remains takes an image and finds images like it. Its limits are measured and stated
on `MATCH_FLOOR` below.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from archiv.images.contracts import ImageSearchResult, NearDuplicateGroup, NearDuplicateItem
from archiv.images.embedder import ImageEmbedder, get_default_image_embedder
from archiv.images.index import connect_image_index, image_index_path, unpack_embedding
from archiv.storage.layout import ArchivLayout


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Dot product of two unit L2-normalized vectors."""
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    return max(-1.0, min(1.0, dot))


MATCH_FLOOR = 0.99
"""Below this similarity, nothing is returned. Measured, not chosen.

Measurement, recorded in `docs/plan/steps/S10.md` and asserted in
`tests/test_image_similarity.py`: three groups of generated images, each group being one
image filed four ways -- the original, re-encoded as JPEG at quality 72, resized to half,
and brightened by 12%. On colour-diverse images, no true match fell below 0.9994 and no
unrelated pair rose above 0.9000. This floor sits inside that gap.

What the floor does **not** do, and cannot: on light pages of dark text every pair lands
between 0.9993 and 1.0000, matching or not, so no floor separates them. The floor stops a
weak match being presented as a match. It does nothing about a corpus where the scores
carry no information, and `archiv images find-similar` on scanned documents is exactly
that corpus.

The sample is three groups of four generated images: enough to place a floor inside a gap
of about 0.10, not enough to justify a third decimal place.
"""


class NotAnImageError(ValueError):
    """The path given as a query is missing, or is not an image this embedder can read."""


def find_similar_images(
    image: Path,
    *,
    top_k: int = 10,
    min_score: float = MATCH_FLOOR,
    home: Path | None = None,
    embedder: ImageEmbedder | None = None,
) -> list[ImageSearchResult]:
    """Find indexed images that look like the given one.

    Takes a path, and only a path. The version this replaced accepted either a string or
    a path and guessed which it had been handed by testing whether the string named an
    existing file -- so a typo in a filename silently became a text query and returned
    ranked nonsense instead of an error.
    """

    layout = ArchivLayout.resolve(home)
    index_file = image_index_path(layout)
    if not index_file.is_file():
        return []

    active_embedder = embedder or get_default_image_embedder()

    query_path = Path(image).expanduser()
    if not query_path.is_file():
        raise NotAnImageError(
            f"no such image: {query_path}. This command searches for images that look "
            "like an image you already have, so it needs the path to one. It has no text "
            "query: the one it used to have gave every string a vector and returned "
            "confident-looking results for words like 'xyzzy'."
        )
    try:
        query_vec = active_embedder.embed_image(query_path)
    except OSError as error:
        raise NotAnImageError(
            f"cannot read {query_path} as an image: {type(error).__name__}: {error}"
        ) from error

    candidates: list[tuple[float, str, str, str, int, int]] = []

    with connect_image_index(index_file) as conn:
        conn.row_factory = None
        cursor = conn.execute(
            """
            SELECT object_sha256, media_type, source_name, width, height, dimensions, embedding
            FROM image_embeddings
            """
        )
        for digest, media_type, source_name, width, height, dimensions, blob in cursor:
            candidate_vec = unpack_embedding(blob, dimensions)
            score = _cosine_similarity(query_vec, candidate_vec)
            if score >= min_score:
                candidates.append((score, digest, media_type, source_name, width, height))

    candidates.sort(key=lambda item: item[0], reverse=True)
    results: list[ImageSearchResult] = []

    for score, digest, media_type, source_name, width, height in candidates[:top_k]:
        orig = layout.original_path(digest)
        preview = layout.derived_root(digest) / "previews" / "thumbnail.webp"
        preview_str = str(preview) if preview.is_file() else None

        results.append(
            ImageSearchResult(
                object_sha256=digest,
                score=round(float(score), 4),
                source_name=source_name,
                media_type=media_type,
                width=width,
                height=height,
                original_path=str(orig),
                preview_path=preview_str,
            )
        )

    return results


def find_near_duplicates(
    *,
    threshold: float = 0.95,
    home: Path | None = None,
    embedder: ImageEmbedder | None = None,
) -> list[NearDuplicateGroup]:
    """Find clusters of near-duplicate images with similarity >= threshold."""
    del embedder  # Similarity is computed directly between stored embeddings
    layout = ArchivLayout.resolve(home)
    index_file = image_index_path(layout)
    if not index_file.is_file():
        return []

    items: list[tuple[str, str, list[float]]] = []
    with connect_image_index(index_file) as conn:
        cursor = conn.execute(
            "SELECT object_sha256, source_name, dimensions, embedding FROM image_embeddings"
        )
        for digest, source_name, dimensions, blob in cursor:
            vec = unpack_embedding(blob, dimensions)
            items.append((digest, source_name, vec))

    if len(items) < 2:
        return []

    # Find duplicate pairs using threshold
    adjacency: dict[str, set[str]] = defaultdict(set)
    pairwise_sims: dict[tuple[str, str], float] = {}
    names_by_digest: dict[str, str] = {digest: name for digest, name, _ in items}

    for i in range(len(items)):
        dig_i, _, vec_i = items[i]
        for j in range(i + 1, len(items)):
            dig_j, _, vec_j = items[j]
            sim = _cosine_similarity(vec_i, vec_j)
            if sim >= threshold:
                adjacency[dig_i].add(dig_j)
                adjacency[dig_j].add(dig_i)
                pairwise_sims[(min(dig_i, dig_j), max(dig_i, dig_j))] = sim

    # Connected components
    visited: set[str] = set()
    clusters: list[list[str]] = []

    for digest, _, _ in items:
        if digest in visited or digest not in adjacency:
            continue
        cluster: list[str] = []
        queue = [digest]
        visited.add(digest)
        while queue:
            node = queue.pop(0)
            cluster.append(node)
            for neighbor in adjacency[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        if len(cluster) >= 2:
            clusters.append(cluster)

    groups: list[NearDuplicateGroup] = []
    for cluster in clusters:
        lead = cluster[0]
        lead_name = names_by_digest.get(lead, "unknown")
        members: list[NearDuplicateItem] = []
        max_sim = 0.0

        for m in cluster[1:]:
            sim = pairwise_sims.get(
                (min(lead, m), max(lead, m)),
                pairwise_sims.get((min(m, lead), max(m, lead)), 1.0),
            )
            max_sim = max(max_sim, sim)
            members.append(
                NearDuplicateItem(
                    object_sha256=m,
                    source_name=names_by_digest.get(m, "unknown"),
                    similarity_to_lead=round(float(sim), 4),
                )
            )

        groups.append(
            NearDuplicateGroup(
                lead_sha256=lead,
                lead_source_name=lead_name,
                members=members,
                max_similarity=round(float(max_sim), 4),
            )
        )

    return groups
