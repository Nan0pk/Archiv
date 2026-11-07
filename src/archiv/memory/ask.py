"""Answering a question: entry (a model), walk (plain code), exit (a model)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from archiv.memory.build import passage_vectors_base
from archiv.memory.extract import first_json_object, ident
from archiv.memory.merge import canonical_ids
from archiv.memory.model import Complete
from archiv.memory.passages import Passage, load_passages
from archiv.memory.store import cache_key, cached, open_memory, remember
from archiv.memory.vectors import Embedder, VectorIndex
from archiv.search.service import search_documents
from archiv.storage.layout import ArchivLayout

if TYPE_CHECKING:
    from archiv.memory.vectors import Vectors

ENTRY_PROMPT_VERSION = "1"
EXIT_PROMPT_VERSION = "1"
NOTHING_FOUND = "The collection has nothing that bears on this question."

_LABELS = re.compile(r"\[(P\d+(?:\s*,\s*P\d+)*)\]")


def entry_prompt(question: str) -> str:
    return (
        "A user is asking the question below of a collection of documents. Say how the "
        "subject of the question might be written in those documents: other names, "
        "spellings, abbreviations, translations or descriptions. Also list other things "
        "that are likely to be connected to it.\n"
        'Answer with JSON only: {"forms": ["..."], "related": ["..."]}\n\n'
        f"Question: {question}"
    )


def parse_entry(answer: str) -> list[str]:
    data = first_json_object(answer)
    if data is None:
        return []
    found: list[str] = []
    for key in ("forms", "related"):
        value = data.get(key)
        if isinstance(value, list):
            found.extend(str(item).strip() for item in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return list(dict.fromkeys(item for item in found if item))


def _in(ids: Iterable[int]) -> tuple[str, list[int]]:
    values = list(ids)
    return ",".join("?" * len(values)), values


def walk(
    conn: sqlite3.Connection,
    *,
    forms: list[str],
    question_vec: Vectors | None,
    vectors: VectorIndex | None,
    by_id: dict[str, Passage],
    home: Path | None,
    hops: int = 2,
    max_passages: int = 60,
    floor: float = 0.55,
    vector_k: int = 10,
    text_limit: int = 10,
) -> list[Passage]:
    """Collect passages by following the web outward from the forms, then add text and
    meaning matches. No model is involved."""
    canon = canonical_ids(conn)
    members: dict[int, list[int]] = {}
    for thing_id, root in canon.items():
        members.setdefault(root, []).append(thing_id)
    wanted = {ident(f) for f in forms}
    seeds = {
        canon[int(r[0])]
        for r in conn.execute("SELECT thing_id, ident FROM things").fetchall()
        if str(r[1]) in wanted
    }
    ordered: list[str] = []
    visited: set[int] = set()
    level = seeds
    for _ in range(hops + 1):
        if not level:
            break
        visited |= level
        things = [t for root in level for t in members[root]]
        marks, params = _in(things)
        for (segment_id,) in conn.execute(
            f"SELECT segment_id FROM forms WHERE thing_id IN ({marks}) ORDER BY segment_id",
            params,
        ):
            ordered.append(str(segment_id))
        nxt: set[int] = set()
        for source, target, segment_id in conn.execute(
            f"SELECT source_id, target_id, segment_id FROM relations "
            f"WHERE source_id IN ({marks}) OR target_id IN ({marks}) ORDER BY segment_id",
            params + params,
        ):
            ordered.append(str(segment_id))
            nxt.add(canon[int(source)])
            nxt.add(canon[int(target)])
        level = nxt - visited
    for form in forms:
        if form.strip():
            for result in search_documents(form, home=home, limit=text_limit):
                ordered.append(result.citation.segment_id)
    if vectors is not None and question_vec is not None:
        ordered.extend(s for s, _ in vectors.nearest(question_vec, k=vector_k, floor=floor))
    unique = dict.fromkeys(s for s in ordered if s in by_id)
    return [by_id[s] for s in list(unique)[:max_passages]]


def exit_prompt(question: str, passages: list[Passage]) -> str:
    labelled = "\n".join(f"[P{i}] {p.text}" for i, p in enumerate(passages, start=1))
    return (
        "Write a plain-language report that answers the question using only the passages "
        "below. Organise it around whatever the passages show matters for the question; "
        "do not use a fixed template. End every sentence with the labels of the passages "
        "that support it, like [P2] or [P1][P3]. If the passages leave part of the "
        "question unanswered, say so. Add nothing from outside the passages.\n\n"
        f"Question: {question}\n\n{labelled}"
    )


@dataclass
class Report:
    text: str
    cited: list[Passage]
    unknown_labels: list[str]
    uncited_sentences: int
    labels: list[int] = field(default_factory=lambda: list[int]())


def check_report(answer: str, passages: list[Passage]) -> Report:
    numbers: list[int] = []
    for match in _LABELS.finditer(answer):
        numbers.extend(int(part.strip()[1:]) for part in match.group(1).split(","))
    cited_numbers = list(dict.fromkeys(n for n in numbers if 1 <= n <= len(passages)))
    unknown = list(dict.fromkeys(f"P{n}" for n in numbers if not 1 <= n <= len(passages)))
    uncited = 0
    for sentence in re.split(r"(?<=[.!?])\s+", answer.strip()):
        if len(sentence.split()) >= 4 and not sentence.lstrip().startswith("#"):
            uncited += 0 if _LABELS.search(sentence) else 1
    return Report(
        text=answer.strip(),
        cited=[passages[n - 1] for n in cited_numbers],
        unknown_labels=unknown,
        uncited_sentences=uncited,
        labels=cited_numbers,
    )


def ask(
    question: str,
    *,
    home: Path | None,
    complete: Complete,
    model_label: str,
    embedder: Embedder | None,
) -> Report:
    layout = ArchivLayout.resolve(home)
    with open_memory(layout) as conn:
        prompt = entry_prompt(question)
        key = cache_key(question, model_label, ENTRY_PROMPT_VERSION, prompt)
        answer = cached(conn, key)
        if answer is None:
            answer = complete(prompt)
            if first_json_object(answer) is not None:
                remember(conn, key, answer)
        forms = parse_entry(answer)
        by_id = {p.segment_id: p for p in load_passages(home)}
        vectors: VectorIndex | None = None
        question_vec = None
        base = passage_vectors_base(layout)
        if embedder is not None and Path(f"{base}.ids.json").is_file():
            stored = VectorIndex.load(base)
            if stored.embedder == embedder.name:
                vectors, question_vec = stored, embedder.embed([question])[0]
        found = walk(
            conn, forms=forms, question_vec=question_vec, vectors=vectors, by_id=by_id, home=home
        )
        if not found:
            return Report(NOTHING_FOUND, [], [], 0)
        prompt = exit_prompt(question, found)
        key = cache_key(question, model_label, EXIT_PROMPT_VERSION, prompt)
        answer = cached(conn, key)
        if answer is None:
            answer = complete(prompt)
            remember(conn, key, answer)
    return check_report(answer, found)
