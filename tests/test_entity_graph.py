# pyright: reportUnknownMemberType=false
"""Acceptance tests for the evidence-backed entity graph and cross-corpus queries."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
from typer.testing import CliRunner

from archiv.cli import app
from archiv.faces.clustering import scan_and_cluster_faces
from archiv.faces.config import save_face_config
from archiv.faces.contracts import FaceConfig
from archiv.graph.builder import rebuild_graph
from archiv.graph.queries import get_entity_profile, query_cross_corpus
from archiv.graph.storage import connect_graph_index, graph_index_path
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _draw_synthetic_face(
    skin_color: tuple[int, int, int],
    eye_color: tuple[int, int, int],
    mouth_color: tuple[int, int, int],
    hair_color: tuple[int, int, int],
) -> Image.Image:
    img = Image.new("RGB", (128, 128), color=(30, 30, 40))
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 16, 98, 40), fill=hair_color)
    draw.ellipse((36, 24, 92, 96), fill=skin_color)
    draw.ellipse((48, 48, 56, 56), fill=eye_color)
    draw.ellipse((72, 48, 80, 56), fill=eye_color)
    draw.rectangle((52, 76, 76, 82), fill=mouth_color)
    return img


def test_entity_graph_cross_corpus_queries_and_citations(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    save_face_config(FaceConfig(opt_in=True), home=home)

    # 1. Create Photograph 1 (1998): Ada Lovelace
    photo_ada = tmp_path / "ada_lovelace_1998.png"
    img_ada = _draw_synthetic_face(
        skin_color=(210, 160, 130),
        eye_color=(20, 20, 150),
        mouth_color=(180, 70, 70),
        hair_color=(50, 30, 10),
    )
    img_ada.save(photo_ada)

    # 2. Create Photograph 2 (2004): Charles Babbage
    photo_babbage = tmp_path / "charles_babbage_2004.jpg"
    img_babbage = _draw_synthetic_face(
        skin_color=(160, 110, 80),
        eye_color=(10, 10, 10),
        mouth_color=(130, 50, 50),
        hair_color=(10, 10, 10),
    )
    exif = img_babbage.getexif()
    exif[315] = "Charles Babbage"
    img_babbage.save(photo_babbage, format="JPEG", exif=exif)

    # 3. Create Document 1 mentioning Ada Lovelace
    doc_ada = tmp_path / "computing_history_notes.txt"
    doc_ada.write_text(
        "Ada Lovelace published the first computer algorithm for the computing engine.",
        encoding="utf-8",
    )

    # 4. Create Document 2 mentioning Charles Babbage and Ada Lovelace together
    doc_joint = tmp_path / "analytical_engine_report.txt"
    doc_joint.write_text(
        "In year 1999, Charles Babbage collaborated with Ada Lovelace on computing concepts.",
        encoding="utf-8",
    )

    # Ingest all files into Archiv
    ingest_file(photo_ada, home=home)
    ingest_file(photo_babbage, home=home)
    ingest_file(doc_ada, home=home)
    ingest_file(doc_joint, home=home)

    # Scan faces
    faces_detected, total_clusters = scan_and_cluster_faces(home=home, threshold=0.96)
    assert faces_detected == 2
    assert total_clusters == 2

    # Rebuild entity graph
    nodes_count, edges_count = rebuild_graph(home=home)
    assert nodes_count > 0
    assert edges_count > 0

    layout = ArchivLayout.resolve(home)
    db_path = graph_index_path(layout)
    assert db_path.is_file()

    # Verify no edge exists without citations
    with connect_graph_index(db_path) as conn:
        edges = conn.execute("SELECT * FROM edges").fetchall()
        for edge_row in edges:
            cits = json.loads(str(edge_row["citations_json"]))
            assert len(cits) > 0, f"Edge {edge_row['edge_id']} has no citations!"
            for c in cits:
                assert "object_sha256" in c
                assert "source_name" in c

    # Execute Marquee Query 1: Persons in photographs between 1995 and 2000
    results_1995_2000 = query_cross_corpus(date_from=1995, date_to=2000, home=home)
    names_1995_2000 = [r.person_name for r in results_1995_2000]
    assert "Ada Lovelace" in names_1995_2000
    assert "Charles Babbage" not in names_1995_2000

    ada_res = next(r for r in results_1995_2000 if r.person_name == "Ada Lovelace")
    assert len(ada_res.photographs) >= 1
    assert ada_res.photographs[0].year == 1998
    assert "ada_lovelace_1998.png" in ada_res.photographs[0].image_name
    assert len(ada_res.mentioning_documents) >= 2
    doc_names = [d.document_name for d in ada_res.mentioning_documents]
    assert "computing_history_notes.txt" in doc_names
    assert "analytical_engine_report.txt" in doc_names

    # Execute Marquee Query 2: Persons in photographs between 2001 and 2010
    results_2001_2010 = query_cross_corpus(date_from=2001, date_to=2010, home=home)
    names_2001_2010 = [r.person_name for r in results_2001_2010]
    assert "Charles Babbage" in names_2001_2010
    assert "Ada Lovelace" not in names_2001_2010

    babbage_res = next(r for r in results_2001_2010 if r.person_name == "Charles Babbage")
    assert len(babbage_res.photographs) >= 1
    assert babbage_res.photographs[0].year == 2004
    assert any(
        "analytical_engine_report.txt" in d.document_name for d in babbage_res.mentioning_documents
    )

    # Test Entity Profile 360 view
    ada_profile = get_entity_profile("Ada Lovelace", home=home)
    assert ada_profile is not None
    assert ada_profile.entity.canonical_name == "Ada Lovelace"
    assert len(ada_profile.appearances) >= 1
    assert len(ada_profile.mentions) >= 2
    # Verify co-occurrence with Charles Babbage
    co_names = [co["entity_name"] for co in ada_profile.co_occurrences]
    assert "Charles Babbage" in co_names

    # Test CLI subcommands
    res_stats = runner.invoke(app, ["graph", "stats", "--home", str(home), "--json"])
    assert res_stats.exit_code == 0
    assert '"total_nodes":' in res_stats.output
    assert '"total_edges":' in res_stats.output

    res_cli_query = runner.invoke(
        app,
        ["graph", "query", "--date-from", "1995", "--date-to", "2000", "--home", str(home)],
    )
    assert res_cli_query.exit_code == 0
    assert "Ada Lovelace" in res_cli_query.output

    res_cli_entity = runner.invoke(
        app,
        ["graph", "entity", "Ada Lovelace", "--home", str(home)],
    )
    assert res_cli_entity.exit_code == 0
    assert "Photograph Appearances" in res_cli_entity.output
    assert "Document Mentions" in res_cli_entity.output
    assert "Co-occurring Entities" in res_cli_entity.output


def test_graph_rebuild_is_completely_deterministic(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    doc = tmp_path / "sample.txt"
    doc.write_text("Grace Hopper created the first compiler in 1952.", encoding="utf-8")
    ingest_file(doc, home=home)

    n1, e1 = rebuild_graph(home=home)
    n2, e2 = rebuild_graph(home=home)
    assert n1 == n2
    assert e1 == e2
