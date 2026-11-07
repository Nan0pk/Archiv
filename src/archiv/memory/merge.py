"""Deciding which written forms are the same thing across documents.

Stage one proposes pairs cheaply from meaning vectors. Stage two asks the model to
confirm each pair from the passages, and keeps its reason.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from archiv.memory.extract import first_json_object
from archiv.memory.model import Complete
from archiv.memory.store import cache_key, cached, remember
from archiv.memory.vectors import Embedder, VectorIndex

MERGE_PROMPT_VERSION = "1"
_FORMS_SHOWN = 8
_PASSAGES_SHOWN = 3


def _forms(conn: sqlite3.Connection, thing_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT form FROM forms WHERE thing_id = ? ORDER BY form LIMIT ?",
        (thing_id, _FORMS_SHOWN),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _segments(conn: sqlite3.Connection, thing_id: int, limit: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT segment_id FROM forms WHERE thing_id = ? ORDER BY segment_id LIMIT ?",
        (thing_id, limit),
    ).fetchall()
    return [str(r[0]) for r in rows]


def thing_texts(
    conn: sqlite3.Connection, passage_text: Mapping[str, str], *, context_passages: int = 2
) -> dict[int, str]:
    """For each thing: its written forms, then a little of the text around it."""
    out: dict[int, str] = {}
    for (thing_id,) in conn.execute("SELECT thing_id FROM things ORDER BY thing_id").fetchall():
        context = [
            passage_text[s][:300]
            for s in _segments(conn, int(thing_id), context_passages)
            if s in passage_text
        ]
        out[int(thing_id)] = " / ".join(_forms(conn, int(thing_id))) + ". " + " ".join(context)
    return out


def propose_pairs(
    conn: sqlite3.Connection,
    embedder: Embedder,
    passage_text: Mapping[str, str],
    *,
    k: int = 3,
    floor: float = 0.80,
    max_pairs: int = 500,
) -> list[tuple[int, int, float]]:
    texts = thing_texts(conn, passage_text)
    if len(texts) < 2:
        return []
    ids = list(texts)
    vectors = embedder.embed([texts[i] for i in ids])
    index = VectorIndex(ids=[str(i) for i in ids], vectors=vectors)
    decided = {
        (int(a), int(b)) for a, b in conn.execute("SELECT a_id, b_id FROM merges").fetchall()
    }
    # Two things found in the same passage are neighbours, not candidates to be the same
    # thing: they share their surrounding text, so their vectors look alike for that reason.
    passages: dict[int, set[str]] = {}
    for thing_id, segment_id in conn.execute("SELECT thing_id, segment_id FROM forms"):
        passages.setdefault(int(thing_id), set()).add(str(segment_id))
    best: dict[tuple[int, int], float] = {}
    for position, thing_id in enumerate(ids):
        for other, score in index.nearest(
            vectors[position], k=k, floor=floor, exclude={str(thing_id)}
        ):
            pair = (min(thing_id, int(other)), max(thing_id, int(other)))
            if pair in decided or passages.get(pair[0], set()) & passages.get(pair[1], set()):
                continue
            best[pair] = max(score, best.get(pair, 0.0))
    ranked = sorted(best.items(), key=lambda item: (-item[1], item[0]))[:max_pairs]
    return [(a, b, score) for (a, b), score in ranked]


def merge_prompt(
    conn: sqlite3.Connection, a: int, b: int, passage_text: Mapping[str, str]
) -> tuple[str, list[str]]:
    cited: list[str] = []

    def side(thing_id: int, name: str) -> str:
        lines = [f"{name} is written as: " + "; ".join(_forms(conn, thing_id))]
        for segment_id in _segments(conn, thing_id, _PASSAGES_SHOWN):
            if segment_id in passage_text:
                cited.append(segment_id)
                lines.append(f'  from the text: "{passage_text[segment_id][:400]}"')
        return "\n".join(lines)

    body = side(a, "Side A") + "\n\n" + side(b, "Side B")
    prompt = (
        "Do Side A and Side B refer to the same thing? They may be written differently, in "
        "different words or languages, but answer yes only if the text supports it.\n\n"
        f"{body}\n\n"
        'Answer with JSON only: {"same": true or false, "reason": "one short sentence"}'
    )
    return prompt, list(dict.fromkeys(cited))


def confirm_pairs(
    conn: sqlite3.Connection,
    pairs: list[tuple[int, int, float]],
    complete: Complete,
    model_label: str,
    passage_text: Mapping[str, str],
) -> int:
    """Ask the model about each pair. Returns how many were confirmed as the same thing."""
    confirmed = 0
    for a, b, _score in pairs:
        prompt, segment_ids = merge_prompt(conn, a, b, passage_text)
        key = cache_key(f"{a}:{b}", model_label, MERGE_PROMPT_VERSION, prompt)
        answer = cached(conn, key)
        fresh = answer is None
        if answer is None:
            answer = complete(prompt)
        verdict = first_json_object(answer)
        if verdict is None or not isinstance(verdict.get("same"), bool):
            continue
        if fresh:
            remember(conn, key, answer)
        same = bool(verdict["same"])
        conn.execute(
            "INSERT OR REPLACE INTO merges (a_id, b_id, same, reason, segment_ids_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (a, b, int(same), str(verdict.get("reason", "")), json.dumps(segment_ids)),
        )
        confirmed += int(same)
    return confirmed


def canonical_ids(conn: sqlite3.Connection) -> dict[int, int]:
    """Map every thing to the smallest id among the things confirmed to be the same."""
    parent: dict[int, int] = {
        int(r[0]): int(r[0]) for r in conn.execute("SELECT thing_id FROM things")
    }

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in conn.execute("SELECT a_id, b_id FROM merges WHERE same = 1").fetchall():
        root_a, root_b = find(int(a)), find(int(b))
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)
    return {thing_id: find(thing_id) for thing_id in parent}
