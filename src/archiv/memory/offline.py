"""Finding the names a collection keeps repeating, with no model and no word list.

A name, an abbreviation or a technical term is a word or a short run of words that the
collection itself repeats, in more than one document, and that is not everywhere. Words that
are everywhere ("of", "the", "and", in any language) are what a word list would remove; here
they are removed by how often they occur, so the same rule works for any script, including
scripts without capital letters.

What is kept is only a proposal: a phrase that happens to repeat is stored as a thing, and the
question-time walk decides what to do with it. Nothing here claims two phrases are the same
thing.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict

from archiv.memory.extract import Extraction, Mention, Relation, ident
from archiv.memory.passages import Passage

_WORD = re.compile(r"\w+(?:[-'’.]\w+)*")
_LETTER = re.compile(r"[^\W\d_]")


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def offline_extractions(
    passages: list[Passage],
    *,
    max_words: int = 3,
    hub_share: float = 0.02,
    min_documents: int = 2,
    per_passage: int = 12,
) -> dict[str, Extraction]:
    """For every document, the repeated phrases in each passage and how they appear together.

    A phrase of up to `max_words` words is kept when it is in at least `min_documents` documents
    and in no more passages than `hub_share` of the collection (a phrase found everywhere says
    nothing). A phrase has to start and end with a word that is not found everywhere and holds
    a letter; in between anything may stand ("Bank of Spring"). A phrase that sits inside a
    longer kept phrase and is found in the same documents is dropped. Two phrases in the same
    passage are connected with "appears with"; only the `per_passage` longest are connected, as
    the model-read build does.
    """
    hub_limit = max(10, int(hub_share * len(passages)))
    tokens = [[(word, ident(word)) for word in _words(p.text)] for p in passages]
    word_passages: dict[str, int] = defaultdict(int)
    for row in tokens:
        for key in {key for _, key in row}:
            word_passages[key] += 1

    def edge_ok(key: str) -> bool:
        return len(key) >= 3 and bool(_LETTER.search(key)) and 0 < word_passages[key] <= hub_limit

    passage_count: dict[str, int] = defaultdict(int)
    documents: dict[str, set[str]] = defaultdict(set)
    shown: list[dict[str, str]] = []
    for passage, row in zip(passages, tokens, strict=True):
        here: dict[str, str] = {}
        for size in range(1, max_words + 1):
            for start in range(len(row) - size + 1):
                if not (edge_ok(row[start][1]) and edge_ok(row[start + size - 1][1])):
                    continue
                key = " ".join(key for _, key in row[start : start + size])
                here.setdefault(key, " ".join(word for word, _ in row[start : start + size]))
        for key in here:
            passage_count[key] += 1
            documents[key].add(passage.object_sha256)
        shown.append(here)

    kept = {
        key
        for key, count in passage_count.items()
        if count <= hub_limit and len(documents[key]) >= min_documents
    }
    out: dict[str, list[tuple[list[Mention], list[Relation]]]] = defaultdict(list)
    for passage, here in zip(passages, shown, strict=True):
        found = [key for key in here if key in kept]
        found = [
            key
            for key in found
            if not any(
                other != key
                and f" {key} " in f" {other} "
                and len(documents[other]) == len(documents[key])
                for other in found
            )
        ]
        found.sort(key=lambda key: (-len(key), key))
        mentions = [Mention(here[key], "", passage.segment_id) for key in found]
        linked = [here[key] for key in found[:per_passage]]
        relations = [
            Relation(a, "appears with", b, passage.segment_id)
            for a, b in itertools.combinations(linked, 2)
        ]
        out[passage.object_sha256].append((mentions, relations))
    return {
        sha: Extraction(
            tuple(m for ms, _ in items for m in ms), tuple(r for _, rs in items for r in rs), 0
        )
        for sha, items in out.items()
    }
