"""Building the memory: read every document once through the model, caching each answer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from archiv.memory.extract import (
    EXTRACT_PROMPT_VERSION,
    Extraction,
    Mention,
    Relation,
    batch_passages,
    extraction_prompt,
    parse_extraction,
)
from archiv.memory.merge import confirm_pairs, propose_pairs
from archiv.memory.model import Complete
from archiv.memory.offline import abbreviation_pairs, offline_extractions
from archiv.memory.passages import Passage, load_passages, passages_by_object
from archiv.memory.store import (
    cache_key,
    cached,
    open_memory,
    record_built,
    remember,
    store_extraction,
    thing_id_for,
)
from archiv.memory.vectors import Embedder, VectorIndex, vectors_match, weights_of
from archiv.storage.layout import ArchivLayout


@dataclass
class BuildSummary:
    documents: int
    model_calls: int
    cache_hits: int
    failed_batches: int
    things: int
    relations: int
    merges_confirmed: int = 0
    earlier_merges: int = 0  # confirmed by a model in an earlier build and still in force


def build_memory(
    *,
    home: Path | None,
    complete: Complete,
    model_label: str,
    max_chars: int = 6000,
    embedder: Embedder | None = None,
    max_merge_pairs: int = 500,
    merge_floor: float = 0.80,
) -> BuildSummary:
    layout = ArchivLayout.resolve(home)
    passages = load_passages(home)
    grouped = passages_by_object(passages)
    calls = hits = failed = 0
    with open_memory(layout) as conn:
        for object_sha256 in sorted(grouped):
            mentions: list[Mention] = []
            relations: list[Relation] = []
            dropped = 0
            for batch in batch_passages(grouped[object_sha256], max_chars=max_chars):
                prompt = extraction_prompt(batch)
                key = cache_key(object_sha256, model_label, EXTRACT_PROMPT_VERSION, prompt)
                answer = cached(conn, key)
                if answer is None:
                    answer = complete(prompt)
                    calls += 1
                    parsed = parse_extraction(answer, batch)
                    if parsed is not None:
                        remember(conn, key, answer)
                else:
                    hits += 1
                    parsed = parse_extraction(answer, batch)
                if parsed is None:
                    failed += 1
                    continue
                mentions.extend(parsed.mentions)
                relations.extend(parsed.relations)
                dropped += parsed.dropped
            store_extraction(
                conn, object_sha256, Extraction(tuple(mentions), tuple(relations), dropped)
            )
            conn.commit()
        record_built(conn, (p.segment_id for p in passages))
        things = int(conn.execute("SELECT COUNT(*) FROM things").fetchone()[0])
        relation_count = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
    merged = 0
    if embedder is not None:
        embed_passages(layout, passages, embedder)
        passage_text = {p.segment_id: p.text for p in passages}
        with open_memory(layout) as conn:
            pairs = propose_pairs(
                conn, embedder, passage_text, floor=merge_floor, max_pairs=max_merge_pairs
            )
            merged = confirm_pairs(conn, pairs, complete, model_label, passage_text)
    return BuildSummary(
        documents=len(grouped),
        model_calls=calls,
        cache_hits=hits,
        failed_batches=failed,
        things=things,
        relations=relation_count,
        merges_confirmed=merged,
    )


def build_memory_offline(*, home: Path | None, embedder: Embedder | None = None) -> BuildSummary:
    """Build the memory with no model: the collection's own repeated phrases, and meaning vectors.

    Nothing is read by a model and nothing is merged here; whether two written forms are the same
    thing is left to the question-time walk. The model-reading build is `build_memory`.
    """
    layout = ArchivLayout.resolve(home)
    passages = load_passages(home)
    extractions = offline_extractions(passages)
    with open_memory(layout) as conn:
        for object_sha256 in sorted(passages_by_object(passages)):
            extraction = extractions.get(object_sha256, Extraction((), (), 0))
            store_extraction(conn, object_sha256, extraction)
        # Where the text defines an abbreviation ("Long Name (LN)"), both written forms are the
        # same thing, so that is stored as a confirmed merge. A rebuild starts from none of its own
        # earlier merges, so a pair the text no longer supports does not stay.
        conn.execute("DELETE FROM merges WHERE reason LIKE 'the text defines %'")
        for long, short, passage in abbreviation_pairs(passages):
            ids = [thing_id_for(conn, form) for form in (long, short)]
            for form, thing_id in zip((long, short), ids, strict=True):
                conn.execute(
                    "INSERT INTO forms (thing_id, form, kind, segment_id, object_sha256) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (thing_id, form, "", passage.segment_id, passage.object_sha256),
                )
            conn.execute(
                "INSERT OR REPLACE INTO merges (a_id, b_id, same, reason, segment_ids_json) "
                "VALUES (?, ?, 1, ?, ?)",
                (
                    ids[0],
                    ids[1],
                    f'the text defines "{short}" as "{long}"',
                    json.dumps([passage.segment_id]),
                ),
            )
        record_built(conn, (p.segment_id for p in passages))
        conn.commit()
        things = int(conn.execute("SELECT COUNT(*) FROM things").fetchone()[0])
        relation_count = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
        earlier = int(
            conn.execute(
                "SELECT COUNT(*) FROM merges "
                "WHERE same = 1 AND reason NOT LIKE 'the text defines %'"
            ).fetchone()[0]
        )
    if embedder is not None:
        embed_passages(layout, passages, embedder)
    return BuildSummary(
        documents=len(passages_by_object(passages)),
        model_calls=0,
        cache_hits=0,
        failed_batches=0,
        things=things,
        relations=relation_count,
        earlier_merges=earlier,
    )


_COPY_CHUNK = 50_000


def passage_vectors_base(layout: ArchivLayout) -> Path:
    return layout.indexes / "memory-passages"


def embed_passages(layout: ArchivLayout, passages: list[Passage], embedder: Embedder) -> None:
    """Embed only the passages that have no stored vector yet, and keep the rest as they are.

    A passage id includes a hash of the passage text, so a stored vector for an id is still
    right for it. Adding one document to a large collection therefore embeds that document's
    passages, not the whole collection again. Vectors made by a different embedder are not
    reused, and neither are stored files whose row count does not match their id list (a
    save that was cut off between the two files).
    """
    if not passages:
        return
    import numpy as np

    base = passage_vectors_base(layout)
    ids = [p.segment_id for p in passages]
    stored: VectorIndex | None = None
    if Path(f"{base}.ids.json").is_file():
        candidate = VectorIndex.load(base)
        if vectors_match(candidate, embedder) and len(candidate.vectors) == len(candidate.ids):
            if candidate.ids == ids:
                return
            stored = candidate
    position = {identifier: row for row, identifier in enumerate(ids)}
    reusable = [] if stored is None else [i for i in stored.ids if i in position]
    have = set(reusable)
    new = [p for p in passages if p.segment_id not in have]
    fresh = embedder.embed([p.text for p in new]) if new else None
    width = fresh.shape[1] if fresh is not None else stored.vectors.shape[1]  # type: ignore[union-attr]
    # One output array, filled in place: a copy of every row at once would triple the memory.
    out = np.empty((len(ids), width), dtype=np.float32)
    if stored is not None and reusable:
        source = {identifier: row for row, identifier in enumerate(stored.ids)}
        for start in range(0, len(reusable), _COPY_CHUNK):
            chunk = reusable[start : start + _COPY_CHUNK]
            out[[position[i] for i in chunk]] = stored.vectors[[source[i] for i in chunk]]
    if fresh is not None:
        out[[position[p.segment_id] for p in new]] = fresh
    # The hash is recorded only when every row was made by these weights. Rows reused from vectors
    # that carried no hash may have been made by other weights of the same name, so the file is
    # then left unhashed and a hash is never certified for rows that were not checked.
    certain = not reusable or (stored is not None and bool(stored.weights))
    VectorIndex(
        ids=ids,
        vectors=out,
        embedder=embedder.name,
        weights=weights_of(embedder) if certain else "",
    ).save(base)
