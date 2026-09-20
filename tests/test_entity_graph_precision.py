"""S12 acceptance tests for honest graph entity and confidence output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app
from archiv.faces.storage import (
    confirm_cluster_name,
    connect_face_index,
    face_index_path,
    pack_vector,
)
from archiv.graph.builder import rebuild_graph
from archiv.graph.storage import connect_graph_index, graph_index_path
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _build_false_positive_graph(tmp_path: Path, *, confirmed_person: bool = False) -> Path:
    home = tmp_path / "archiv_home"
    source = tmp_path / "minutes.txt"
    source.write_text(
        "New Delhi discussed Machine Learning with Legal Counsel and Agenda Item. "
        "Alice Smith attended.",
        encoding="utf-8",
    )
    ingest_file(source, home=home)
    if confirmed_person:
        layout = ArchivLayout.resolve(home)
        cluster_id = "confirmed-alice-smith"
        now = datetime.now(UTC).isoformat()
        with connect_face_index(face_index_path(layout)) as conn:
            conn.execute(
                """
                INSERT INTO face_clusters (
                    cluster_id, label, member_count, centroid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (cluster_id, "Person 1", 1, pack_vector([0.0] * 64), now, now),
            )
            conn.commit()
        confirm_cluster_name(layout, cluster_id, "Alice Smith")
    rebuild_graph(home=home)
    return home


def test_known_false_positives_are_not_typed_person(tmp_path: Path) -> None:
    home = _build_false_positive_graph(tmp_path, confirmed_person=True)
    layout = ArchivLayout.resolve(home)
    with connect_graph_index(graph_index_path(layout)) as conn:
        rows = conn.execute(
            "SELECT canonical_name, entity_type FROM nodes WHERE canonical_name IN "
            "('New Delhi', 'Machine Learning', 'Legal Counsel', 'Agenda Item')"
        ).fetchall()
        confirmed = conn.execute(
            "SELECT entity_type FROM nodes WHERE canonical_name = 'Alice Smith'"
        ).fetchone()

    assert rows
    assert all(str(row["entity_type"]) == "candidate_mention" for row in rows)
    assert not any(str(row["entity_type"]) == "person" for row in rows)
    assert confirmed is not None
    assert str(confirmed["entity_type"]) == "person"


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
