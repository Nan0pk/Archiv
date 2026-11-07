from __future__ import annotations

import json

from archiv.memory.extract import (
    batch_passages,
    extraction_prompt,
    ident,
    parse_extraction,
)
from archiv.memory.passages import Passage


def _p(i: int, text: str = "Some neutral passage text for testing purposes.") -> Passage:
    return Passage(
        segment_id=f"{i:064x}",
        object_sha256="a" * 64,
        source_name="doc.txt",
        locator={"line": i},
        text=text,
    )


def test_prompt_labels_passages_and_names_no_kinds() -> None:
    prompt = extraction_prompt([_p(1), _p(2)])
    assert "[P1]" in prompt and "[P2]" in prompt
    lowered = prompt.lower()
    for word in ("person", "organization", "organisation", "location", "place"):
        assert word not in lowered


def test_parse_handles_fenced_and_prose() -> None:
    body = {
        "things": [
            {"form": "North Pier Board", "kind": "a committee", "passages": ["P1"]},
            {"form": "Bridge Plan", "kind": "a proposal", "passages": ["P2"]},
        ],
        "relations": [
            {
                "source": "North Pier Board",
                "relation": "reviewed",
                "target": "Bridge Plan",
                "passage": "P2",
            },
        ],
    }
    answer = "Here is what I found.\n```json\n" + json.dumps(body) + "\n```\nDone."
    batch = [_p(1), _p(2)]
    result = parse_extraction(answer, batch)
    assert result is not None
    assert len(result.mentions) == 2 and len(result.relations) == 1
    assert result.mentions[0].segment_id == batch[0].segment_id
    assert result.relations[0].segment_id == batch[1].segment_id
    assert result.relations[0].phrase == "reviewed"
    assert result.dropped == 0


def test_parse_drops_unknown_passage_and_unmentioned_target() -> None:
    body = {
        "things": [
            {"form": "Alpha", "kind": "k", "passages": ["P1"]},
            {"form": "Beta", "kind": "k", "passages": ["P1"]},
        ],
        "relations": [
            {"source": "Alpha", "relation": "r", "target": "Beta", "passage": "P9"},
            {"source": "Alpha", "relation": "r", "target": "Gamma", "passage": "P1"},
            {"source": "alpha", "relation": "r", "target": "BETA", "passage": "P1"},
        ],
    }
    result = parse_extraction(json.dumps(body), [_p(1)])
    assert result is not None
    assert result.dropped == 2
    assert len(result.relations) == 1


def test_parse_truncated_returns_none() -> None:
    assert parse_extraction('{"things": [{"form": "A"', [_p(1)]) is None


def test_batches_never_split_and_respect_budget() -> None:
    passages = [_p(1, "x" * 4000), _p(2, "y" * 3000), _p(3, "z" * 9000)]
    batches = batch_passages(passages, max_chars=6000)
    assert [[len(p.text) for p in b] for b in batches] == [[4000], [3000], [9000]]


def test_ident_normalises_composition_case_and_space() -> None:
    assert ident("Café  Board") == ident("café board")
