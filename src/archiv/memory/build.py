"""Building the memory: read every document once through the model, caching each answer."""

from __future__ import annotations

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
from archiv.memory.model import Complete
from archiv.memory.passages import load_passages, passages_by_object
from archiv.memory.store import cache_key, cached, open_memory, remember, store_extraction
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


def build_memory(
    *,
    home: Path | None,
    complete: Complete,
    model_label: str,
    max_chars: int = 6000,
) -> BuildSummary:
    layout = ArchivLayout.resolve(home)
    grouped = passages_by_object(load_passages(home))
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
        things = int(conn.execute("SELECT COUNT(*) FROM things").fetchone()[0])
        relation_count = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
    return BuildSummary(
        documents=len(grouped),
        model_calls=calls,
        cache_hits=hits,
        failed_batches=failed,
        things=things,
        relations=relation_count,
    )
