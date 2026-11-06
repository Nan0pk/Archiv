"""Asking a model what a batch of passages mentions, and reading its answer safely."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from typing import cast

from archiv.memory.passages import Passage

EXTRACT_PROMPT_VERSION = "1"


@dataclass(frozen=True)
class Mention:
    form: str
    kind: str
    segment_id: str


@dataclass(frozen=True)
class Relation:
    source: str
    phrase: str
    target: str
    segment_id: str


@dataclass(frozen=True)
class Extraction:
    mentions: tuple[Mention, ...]
    relations: tuple[Relation, ...]
    dropped: int


def ident(form: str) -> str:
    """Identity key for a written form: Unicode-normalised, case-folded, single-spaced."""
    return " ".join(unicodedata.normalize("NFKC", form).casefold().split())


def batch_passages(passages: list[Passage], *, max_chars: int = 6000) -> list[list[Passage]]:
    batches: list[list[Passage]] = []
    current: list[Passage] = []
    size = 0
    for passage in passages:
        if current and size + len(passage.text) > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(passage)
        size += len(passage.text)
    if current:
        batches.append(current)
    return batches


def extraction_prompt(batch: list[Passage]) -> str:
    labelled = "\n".join(f"[P{i}] {p.text}" for i, p in enumerate(batch, start=1))
    return (
        "Read the passages below. List every distinct thing they mention, of any kind.\n"
        "For each thing give the form exactly as written, what kind of thing it is in your "
        "own words, and the passage labels where it appears.\n"
        "Then list every relationship between two of those things that the text states, "
        "described in your own words, with the one passage label that states it.\n"
        "Use only what the passages say. Add nothing from outside them.\n"
        'Answer with JSON only: {"things": [{"form": "...", "kind": "...", '
        '"passages": ["P1"]}], "relations": [{"source": "...", "relation": "...", '
        '"target": "...", "passage": "P1"}]}\n\n'
        f"{labelled}"
    )


def first_json_object(text: str) -> dict[str, object] | None:
    """Return the first complete JSON object in `text`, ignoring prose and code fences."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value: object = decoder.raw_decode(text, start)[0]
        except json.JSONDecodeError:
            value = None
        found = _as_dict(value)
        if found is not None:
            return found
        start = text.find("{", start + 1)
    return None


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in cast(dict[object, object], value).items()}


def _as_list(value: object) -> list[object]:
    return list(cast(list[object], value)) if isinstance(value, list) else []


def _text(item: dict[str, object], key: str) -> str:
    return str(item.get(key, "")).strip()


def parse_extraction(answer: str, batch: list[Passage]) -> Extraction | None:
    data = first_json_object(answer)
    if data is None:
        return None
    labels = {f"P{i}": p.segment_id for i, p in enumerate(batch, start=1)}
    mentions: list[Mention] = []
    dropped = 0
    for raw in _as_list(data.get("things")):
        item = _as_dict(raw)
        if item is None:
            dropped += 1
            continue
        form, kind = _text(item, "form"), _text(item, "kind")
        found = [labels[str(x)] for x in _as_list(item.get("passages")) if str(x) in labels]
        if not form or not found:
            dropped += 1
            continue
        mentions.extend(Mention(form, kind, segment_id) for segment_id in found)
    known = {ident(m.form) for m in mentions}
    relations: list[Relation] = []
    for raw in _as_list(data.get("relations")):
        item = _as_dict(raw)
        if item is None:
            dropped += 1
            continue
        source, target = _text(item, "source"), _text(item, "target")
        phrase, label = _text(item, "relation"), _text(item, "passage")
        if (
            label not in labels
            or not phrase
            or ident(source) not in known
            or ident(target) not in known
        ):
            dropped += 1
            continue
        relations.append(Relation(source, phrase, target, labels[label]))
    return Extraction(tuple(mentions), tuple(relations), dropped)
