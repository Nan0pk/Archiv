"""Answering a question: entry (a model), walk (plain code), exit (a model)."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from archiv.memory.build import passage_vectors_base
from archiv.memory.extract import first_json_object, ident
from archiv.memory.merge import canonical_ids
from archiv.memory.model import Complete
from archiv.memory.passages import Passage, count_literal_passages, load_passages
from archiv.memory.store import cache_key, cached, open_memory, remember
from archiv.memory.vectors import Embedder, VectorIndex
from archiv.search.service import search_documents
from archiv.storage.layout import ArchivLayout

if TYPE_CHECKING:
    from archiv.memory.vectors import Vectors

ENTRY_PROMPT_VERSION = "2"
EXIT_PROMPT_VERSION = "2"
NOTHING_FOUND = "The collection has nothing that bears on this question."

_LABELS = re.compile(r"\[(P\d+(?:\s*,\s*P\d+)*)\]")


def entry_prompt(question: str, samples: list[str] | None = None) -> str:
    grounding = ""
    if samples:
        shown = "\n".join(f"- {sample}" for sample in samples)
        grounding = (
            "Here are a few passages from this collection that are closest in meaning to the "
            "question. The collection may use a word or an abbreviation differently from "
            "everyday use, so rely on what these passages show it to mean:\n"
            f"{shown}\n\n"
        )
    return (
        f"{grounding}"
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


_CHUNK = 400  # SQLite allows only a limited number of ? marks in one statement


def _chunks(items: list[int] | list[str], size: int = _CHUNK) -> Iterator[list[Any]]:
    for position in range(0, len(items), size):
        yield list(items[position : position + size])


def _marks(count: int) -> str:
    return ",".join("?" * count)


def _document_order(passage: Passage) -> tuple[str, str]:
    return passage.source_name, json.dumps(passage.locator, sort_keys=True, default=str)


def walk(
    conn: sqlite3.Connection,
    *,
    forms: list[str],
    question_vec: Vectors | None,
    vectors: VectorIndex | None,
    by_id: dict[str, Passage],
    home: Path | None,
    hops: int = 2,
    max_passages: int = 40,
    floor: float = 0.55,
    vector_k: int = 10,
    text_limit: int = 10,
    max_forms: int = 10,
    derived_seeds: int = 15,
) -> list[Passage]:
    """Collect the passages to hand to the report step. No model is involved.

    Three kinds of passage compete for `max_passages` slots:
    - exact text matches of the starting names, taking at most half the slots;
    - passages reached by following the web outward from those names;
    - passages whose meaning is close to the question.
    Everything except the exact matches is ranked by similarity to the question when
    vectors exist, and in a fixed document order when they do not.
    """
    forms = [form for form in (value.strip() for value in forms) if form][:max_forms]
    canon = canonical_ids(conn)
    members: dict[int, list[int]] = {}
    for thing_id, root in canon.items():
        members.setdefault(root, []).append(thing_id)
    passages_of = {
        int(t): int(c)
        for t, c in conn.execute(
            "SELECT thing_id, COUNT(DISTINCT segment_id) FROM forms GROUP BY thing_id"
        )
    }
    hub_limit = max(10, int(0.02 * len(by_id)))  # a name in more passages than this is everywhere

    def is_hub(root: int) -> bool:
        return max(passages_of.get(t, 0) for t in members[root]) > hub_limit

    literal = list(
        dict.fromkeys(
            result.citation.segment_id
            for form in forms
            for result in search_documents(form, home=home, limit=text_limit)
            if result.citation.segment_id in by_id
        )
    )
    meaning: list[str] = []
    if vectors is not None and question_vec is not None:
        meaning = [
            s for s, _ in vectors.nearest(question_vec, k=vector_k, floor=floor) if s in by_id
        ]

    # Starting points: names equal to a starting name, names that contain it or are part of
    # it (other tools draw name boundaries differently), and, one step out, the other names
    # mentioned in the passages found so far.
    wanted = {ident(form) for form in forms}
    names = {int(t): str(i) for t, i in conn.execute("SELECT thing_id, ident FROM things")}
    seen_in_found: dict[int, int] = defaultdict(int)
    for chunk in _chunks(list(dict.fromkeys(literal + meaning))):
        for thing_id, count in conn.execute(
            "SELECT thing_id, COUNT(DISTINCT segment_id) FROM forms "
            f"WHERE segment_id IN ({_marks(len(chunk))}) GROUP BY thing_id",
            chunk,
        ):
            seen_in_found[int(thing_id)] += int(count)

    def related_to_asked(thing_id: int) -> bool:
        name = names[thing_id]
        return len(name) >= 4 and any(name in want or want in name for want in wanted)

    start = {canon[t] for t, i in names.items() if i in wanted}
    start |= {canon[t] for t in seen_in_found if related_to_asked(t)}
    nearby = sorted(
        (t for t in seen_in_found if canon[t] not in start and passages_of.get(t, 0) <= hub_limit),
        key=lambda t: (-seen_in_found[t], passages_of.get(t, 0), t),
    )[:derived_seeds]
    one_step_out = {canon[t] for t in nearby}

    graph: list[str] = []
    visited: set[int] = set()
    level = set(start)
    for depth in range(hops + 1):
        if depth == 1:
            level |= one_step_out
        level = {root for root in level if root not in visited}
        if not level:
            if depth == 0:
                continue
            break
        visited |= level
        things = [t for root in level for t in members[root]]
        found: list[str] = []
        following: set[int] = set()
        for chunk in _chunks(things):
            marks = _marks(len(chunk))
            found += [
                str(r[0])
                for r in conn.execute(
                    f"SELECT segment_id FROM forms WHERE thing_id IN ({marks})", chunk
                )
            ]
            for column, other in (("source_id", "target_id"), ("target_id", "source_id")):
                for segment_id, neighbour in conn.execute(
                    f"SELECT segment_id, {other} FROM relations WHERE {column} IN ({marks})", chunk
                ):
                    found.append(str(segment_id))
                    following.add(canon[int(neighbour)])
        graph += sorted(
            (s for s in dict.fromkeys(found) if s in by_id), key=lambda s: _document_order(by_id[s])
        )
        level = {root for root in following if not is_hub(root)}

    scores: dict[str, float] = {}
    if vectors is not None and question_vec is not None:
        scores = vectors.scores(list(dict.fromkeys(literal + graph + meaning)), question_vec)

    def ranked(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda s: -scores.get(s, -1.0)) if scores else ids

    exact = ranked(literal)[: max_passages // 2]
    rest = ranked([s for s in dict.fromkeys(meaning + graph + literal) if s not in exact])
    return [by_id[s] for s in (exact + rest)[:max_passages]]


def exit_prompt(
    question: str, passages: list[Passage], totals: list[tuple[str, int, int]] | None = None
) -> str:
    """The prompt for the report step.

    Each passage is shown with the name of its document, which usually carries the date.
    `totals` holds (name, passages in the collection that mention it, how many were given).
    """
    labelled = "\n".join(f"[P{i}] ({p.source_name}) {p.text}" for i, p in enumerate(passages, 1))
    counts = ""
    if totals:
        lines = [
            f"- The collection has {n} passage{'s' if n != 1 else ''} that "
            f"{'mention' if n != 1 else 'mentions'} '{name}'. You have been given {k} of them."
            for name, n, k in totals
        ]
        counts = "Counts you may rely on:\n" + "\n".join(lines) + "\n\n"
    return (
        "Write a plain-language report that answers the question using only the passages "
        "below. Each passage starts with the name of the document it came from, which often "
        "includes its date. Organise the report around whatever the passages show matters "
        "for the question; do not use a fixed template. End every sentence that states "
        "something with the labels of the passages that support it, like [P2] or [P1][P3]. "
        "Do not say how many passages or items the collection holds unless a line under "
        "'Counts you may rely on' gives that number; otherwise say how many you were given. "
        "Put anything about what the passages do not say in a final paragraph that starts "
        "with 'Gaps:'; that paragraph needs no labels. Add nothing from outside the "
        f"passages.\n\n{counts}Question: {question}\n\n{labelled}"
    )


def literal_totals(
    home: Path | None, forms: list[str], given: list[Passage], limit: int = 5
) -> list[tuple[str, int, int]]:
    """For the first starting names that occur in the collection: the true number of passages
    that mention each, and how many of those were given to the report step."""
    totals: list[tuple[str, int, int]] = []
    for form in [f.strip() for f in forms if f.strip()][:10]:
        total = count_literal_passages(home, form)
        if total:
            have = sum(1 for p in given if form.casefold() in p.text.casefold())
            totals.append((form, total, have))
        if len(totals) >= limit:
            break
    return totals


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
    for paragraph in re.split(r"\n\s*\n", answer.strip()):
        if re.match(r"\W*gaps\b", paragraph, re.IGNORECASE):
            continue  # what the passages do not say has nothing to cite
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if len(sentence.split()) >= 6 and not sentence.lstrip().startswith("#"):
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
        by_id = {p.segment_id: p for p in load_passages(home)}
        vectors: VectorIndex | None = None
        question_vec = None
        base = passage_vectors_base(layout)
        if embedder is not None and Path(f"{base}.ids.json").is_file():
            stored = VectorIndex.load(base)
            if stored.embedder == embedder.name:
                vectors, question_vec = stored, embedder.embed([question])[0]
        # Show the entry step what this collection says near the question, so that a term is
        # read the way the collection uses it and not the way everyday use does.
        samples: list[str] = []
        if vectors is not None and question_vec is not None:
            samples = [
                by_id[s].text[:300]
                for s, _ in vectors.nearest(question_vec, k=5, floor=0.3)
                if s in by_id
            ]
        prompt = entry_prompt(question, samples)
        key = cache_key(question, model_label, ENTRY_PROMPT_VERSION, prompt)
        answer = cached(conn, key)
        if answer is None:
            answer = complete(prompt)
            if first_json_object(answer) is not None:
                remember(conn, key, answer)
        forms = parse_entry(answer)
        found = walk(
            conn, forms=forms, question_vec=question_vec, vectors=vectors, by_id=by_id, home=home
        )
        if not found:
            return Report(NOTHING_FOUND, [], [], 0)
        prompt = exit_prompt(question, found, literal_totals(home, forms, found))
        key = cache_key(question, model_label, EXIT_PROMPT_VERSION, prompt)
        answer = cached(conn, key)
        if answer is None:
            answer = complete(prompt)
            remember(conn, key, answer)
    return check_report(answer, found)
