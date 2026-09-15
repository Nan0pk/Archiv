"""S12 acceptance tests for honest graph entity and confidence output."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app
from archiv.graph.builder import rebuild_graph
from archiv.graph.storage import connect_graph_index, graph_index_path
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _build_false_positive_graph(tmp_path: Path) -> Path:
    home = tmp_path / "archiv_home"
    source = tmp_path / "minutes.txt"
    source.write_text(
        "New Delhi discussed Machine Learning with Legal Counsel and Agenda Item.",
        encoding="utf-8",
    )
    ingest_file(source, home=home)
    rebuild_graph(home=home)
    return home


def test_known_false_positives_are_not_typed_person(tmp_path: Path) -> None:
    home = _build_false_positive_graph(tmp_path)
    layout = ArchivLayout.resolve(home)
    with connect_graph_index(graph_index_path(layout)) as conn:
        rows = conn.execute(
            "SELECT canonical_name, entity_type FROM nodes WHERE canonical_name IN "
            "('New Delhi', 'Machine Learning', 'Legal Counsel', 'Agenda Item')"
        ).fetchall()

    assert rows
    assert all(str(row["entity_type"]) == "candidate_mention" for row in rows)
    assert not any(str(row["entity_type"]) == "person" for row in rows)


def test_no_unmeasured_confidence_value_reaches_user_output(tmp_path: Path) -> None:
    home = _build_false_positive_graph(tmp_path)

    human = runner.invoke(app, ["graph", "entity", "New Delhi", "--home", str(home)])
    assert human.exit_code == 0
    assert "candidate_mention" in human.output
    assert "Confidence" not in human.output
    assert "%" not in human.output

    machine = runner.invoke(
        app,
        ["graph", "entity", "New Delhi", "--home", str(home), "--json"],
    )
    assert machine.exit_code == 0
    payload = json.loads(machine.output)
    assert payload["entity"]["entity_type"] == "candidate_mention"
    assert "confidence" not in machine.output.lower()
