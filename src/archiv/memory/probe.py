"""Blind probes: questions drawn at random from the data, never picked by the builder."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Probe:
    thing_id: int
    form: str
    question: str


def draw_probes(conn: sqlite3.Connection, *, n: int, seed: int) -> list[Probe]:
    """Draw `n` things uniformly at random (fewer if there are fewer) and ask about each."""
    rows = [
        (int(r[0]), str(r[1]))
        for r in conn.execute("SELECT thing_id, form FROM things ORDER BY thing_id")
    ]
    chosen = random.Random(seed).sample(rows, min(n, len(rows)))
    return [
        Probe(thing_id, form, f"What does the collection hold about {form}?")
        for thing_id, form in chosen
    ]
