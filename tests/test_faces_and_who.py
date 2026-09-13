"""Acceptance tests for the opt-in clustering-only face workflow."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from archiv.cli import app
from archiv.faces.attributions import attribute_cluster
from archiv.faces.clustering import scan_and_cluster_faces
from archiv.faces.config import BiometricsDisabledError, check_faces_opt_in, save_face_config
from archiv.faces.contracts import FaceConfig, FaceDetection
from archiv.faces.storage import connect_face_index, face_index_path, pack_vector, save_detection
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _seed_cluster(layout: ArchivLayout, source_name: str = "Ada_Lovelace.png") -> str:
    cluster_id = "cluster-1"
    detection = FaceDetection(
        face_id="face-1",
        object_sha256="1" * 64,
        source_name=source_name,
        bbox=[10.0, 10.0, 50.0, 60.0],
        confidence=0.9,
        embedding=[0.0] * 64,
    )
    with connect_face_index(face_index_path(layout)) as conn:
        conn.execute(
            """
            INSERT INTO face_clusters
                (cluster_id, label, member_count, centroid, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cluster_id, "Person 1", 1, pack_vector([0.0] * 64), "now", "now"),
        )
        save_detection(conn, detection, cluster_id=cluster_id)
        conn.commit()
    return cluster_id


def test_biometrics_disabled_by_default_and_opt_in_lifecycle(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    with pytest.raises(BiometricsDisabledError):
        check_faces_opt_in(home)

    res = runner.invoke(app, ["faces", "scan", "--home", str(home)])
    assert res.exit_code == 1
    assert "Opt-in Required" in res.output

    res_status = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert res_status.exit_code == 0
    assert '"opt_in": false' in res_status.output

    res_optin = runner.invoke(app, ["faces", "opt-in", "--home", str(home)])
    assert res_optin.exit_code == 0
    assert "Face analysis enabled" in res_optin.output
    assert check_faces_opt_in(home).opt_in is True

    res_optout = runner.invoke(app, ["faces", "opt-out", "--home", str(home)])
    assert res_optout.exit_code == 0
    assert "Face analysis disabled" in res_optout.output
    with pytest.raises(BiometricsDisabledError):
        check_faces_opt_in(home)

    os.environ["ARCHIV_FACES_OPT_IN"] = "1"
    try:
        assert check_faces_opt_in(home).opt_in is True
    finally:
        del os.environ["ARCHIV_FACES_OPT_IN"]


def test_face_scan_refuses_guesses_but_existing_cluster_can_be_confirmed(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    save_face_config(FaceConfig(opt_in=True), home=home)

    image_path = tmp_path / "Ada_Lovelace_portrait.png"
    Image.new("RGB", (128, 128), (210, 160, 130)).save(image_path)
    ingest_file(image_path, home=home)

    faces_detected, total_clusters = scan_and_cluster_faces(home=home)
    assert faces_detected == 0
    assert total_clusters == 0

    layout = ArchivLayout.resolve(home)
    cluster_id = _seed_cluster(layout)
    attribution = attribute_cluster(layout, cluster_id)
    assert attribution is not None
    assert attribution.status == "unconfirmed"
    assert attribution.confirmed_name is None
    assert attribution.candidates == []

    res_who = runner.invoke(app, ["who", "Person 1", "--home", str(home)])
    assert res_who.exit_code == 0
    assert "Unconfirmed" in res_who.output
    assert "Ada Lovelace" not in res_who.output

    res_confirm = runner.invoke(
        app,
        ["who", "Person 1", "--confirm", "Ada Lovelace", "--home", str(home)],
    )
    assert res_confirm.exit_code == 0

    confirmed = attribute_cluster(layout, cluster_id)
    assert confirmed is not None
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_name == "Ada Lovelace"
    assert confirmed.candidates == []

    res_by_name = runner.invoke(app, ["who", "Ada Lovelace", "--home", str(home), "--json"])
    assert res_by_name.exit_code == 0
    assert '"status": "confirmed"' in res_by_name.output
    assert '"confirmed_name": "Ada Lovelace"' in res_by_name.output

    res_revoke = runner.invoke(
        app,
        ["who", "Ada Lovelace", "--revoke", "--home", str(home)],
    )
    assert res_revoke.exit_code == 0
    updated = attribute_cluster(layout, cluster_id)
    assert updated is not None
    assert updated.status == "unconfirmed"


def test_first_class_erasure_leaves_originals_untouched(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    save_face_config(FaceConfig(opt_in=True), home=home)

    img_file = tmp_path / "image.png"
    Image.new("RGB", (32, 32), (20, 30, 40)).save(img_file)
    ingestion_res = ingest_file(img_file, home=home)
    layout = ArchivLayout.resolve(home)
    orig_path = layout.original_path(ingestion_res.object_sha256)
    original_bytes = orig_path.read_bytes()

    _seed_cluster(layout, source_name=img_file.name)
    status_before = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert '"total_faces": 1' in status_before.output

    res_forget = runner.invoke(app, ["faces", "forget", "--all", "--home", str(home)])
    assert res_forget.exit_code == 0
    assert "Biometric erasure complete" in res_forget.output

    status_after = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert '"total_faces": 0' in status_after.output
    assert '"total_clusters": 0' in status_after.output
    assert orig_path.is_file()
    assert orig_path.read_bytes() == original_bytes
