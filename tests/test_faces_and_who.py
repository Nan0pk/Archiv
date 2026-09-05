# pyright: reportUnknownMemberType=false
"""Acceptance tests for face detection, clustering, attribution, and identity resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from archiv.cli import app
from archiv.faces.attributions import (
    attribute_all_clusters,
    attribute_cluster,
)
from archiv.faces.clustering import scan_and_cluster_faces
from archiv.faces.config import (
    BiometricsDisabledError,
    check_faces_opt_in,
    save_face_config,
)
from archiv.faces.contracts import FaceConfig
from archiv.ingestion import ingest_file
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


def _draw_synthetic_face(
    skin_color: tuple[int, int, int],
    eye_color: tuple[int, int, int],
    mouth_color: tuple[int, int, int],
    hair_color: tuple[int, int, int],
    size: tuple[int, int] = (128, 128),
) -> Image.Image:
    """Generate a lawful synthetic face canvas with distinct geometric features."""
    img = Image.new("RGB", size, color=(30, 30, 40))
    draw = ImageDraw.Draw(img)
    # Hair
    draw.rectangle((30, 16, 98, 40), fill=hair_color)
    # Skin oval
    draw.ellipse((36, 24, 92, 96), fill=skin_color)
    # Eyes
    draw.ellipse((48, 48, 56, 56), fill=eye_color)
    draw.ellipse((72, 48, 80, 56), fill=eye_color)
    # Mouth
    draw.rectangle((52, 76, 76, 82), fill=mouth_color)
    return img


def _save_synthetic_image(
    path: Path,
    skin_color: tuple[int, int, int],
    eye_color: tuple[int, int, int],
    mouth_color: tuple[int, int, int],
    hair_color: tuple[int, int, int],
    artist_exif: str | None = None,
) -> None:
    img = _draw_synthetic_face(skin_color, eye_color, mouth_color, hair_color)
    if artist_exif:
        exif = img.getexif()
        exif[315] = artist_exif  # 315 = Artist
        img.save(path, format="JPEG", exif=exif)
    else:
        img.save(path, format="PNG")


def test_biometrics_disabled_by_default_and_opt_in_lifecycle(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"

    # Default: disabled
    with pytest.raises(BiometricsDisabledError):
        check_faces_opt_in(home)

    # CLI scan fails with exit code 1 when disabled
    res = runner.invoke(app, ["faces", "scan", "--home", str(home)])
    assert res.exit_code == 1
    assert "Opt-in Required" in res.output

    # Status shows disabled
    res_status = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert res_status.exit_code == 0
    assert '"opt_in": false' in res_status.output

    # Opt in via CLI
    res_optin = runner.invoke(app, ["faces", "opt-in", "--home", str(home)])
    assert res_optin.exit_code == 0
    assert "Face analysis enabled" in res_optin.output

    # Now check_faces_opt_in succeeds
    cfg = check_faces_opt_in(home)
    assert cfg.opt_in is True
    assert cfg.opt_in_at is not None

    # Opt out via CLI
    res_optout = runner.invoke(app, ["faces", "opt-out", "--home", str(home)])
    assert res_optout.exit_code == 0
    assert "Face analysis disabled" in res_optout.output
    assert check_faces_opt_in.__name__

    with pytest.raises(BiometricsDisabledError):
        check_faces_opt_in(home)

    # Environment override works
    os.environ["ARCHIV_FACES_OPT_IN"] = "1"
    try:
        cfg_env = check_faces_opt_in(home)
        assert cfg_env.opt_in is True
    finally:
        del os.environ["ARCHIV_FACES_OPT_IN"]


def test_face_detection_clustering_attribution_and_confirmation(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    # Opt in
    save_face_config(FaceConfig(opt_in=True), home=home)

    # Person 1 (Ada Lovelace): 2 images
    ada_img1 = tmp_path / "ada_lovelace_portrait.png"
    _save_synthetic_image(
        ada_img1,
        skin_color=(210, 160, 130),
        eye_color=(20, 20, 150),
        mouth_color=(180, 70, 70),
        hair_color=(50, 30, 10),
    )
    ada_img2 = tmp_path / "ada_lovelace_photo2.png"
    _save_synthetic_image(
        ada_img2,
        skin_color=(215, 165, 135),
        eye_color=(20, 20, 150),
        mouth_color=(185, 75, 75),
        hair_color=(50, 30, 10),
    )

    # Person 2 (Charles Babbage): 1 image with EXIF Artist
    babbage_img = tmp_path / "photo_babbage.jpg"
    _save_synthetic_image(
        babbage_img,
        skin_color=(160, 110, 80),
        eye_color=(10, 10, 10),
        mouth_color=(130, 50, 50),
        hair_color=(10, 10, 10),
        artist_exif="Charles Babbage",
    )

    # Ingest images
    ingest_file(ada_img1, home=home)
    ingest_file(ada_img2, home=home)
    ingest_file(babbage_img, home=home)

    # Scan and cluster faces
    faces_detected, total_clusters = scan_and_cluster_faces(home=home, threshold=0.96)
    assert faces_detected == 3
    assert total_clusters == 2

    layout = ArchivLayout.resolve(home)
    attributions = attribute_all_clusters(home=home)
    assert len(attributions) == 2

    # Verify cluster memberships
    counts = {a.member_count for a in attributions}
    assert 2 in counts
    assert 1 in counts

    # Identify Ada cluster and Babbage cluster
    ada_cluster = next(a for a in attributions if a.member_count == 2)
    babbage_cluster = next(a for a in attributions if a.member_count == 1)

    # Status must initially be unconfirmed (never auto-asserted)
    assert ada_cluster.status == "unconfirmed"
    assert babbage_cluster.status == "unconfirmed"
    assert ada_cluster.confirmed_name is None

    # Candidate hypotheses must cite evidence
    assert len(ada_cluster.candidates) > 0
    top_ada_cand = ada_cluster.candidates[0]
    assert top_ada_cand.name == "Ada Lovelace"
    assert any(c.source_type == "filename" for c in top_ada_cand.supporting_citations)

    assert len(babbage_cluster.candidates) > 0
    top_babbage_cand = babbage_cluster.candidates[0]
    assert top_babbage_cand.name == "Charles Babbage"
    assert any(c.source_type == "exif" for c in top_babbage_cand.supporting_citations)

    # Test CLI 'archiv faces list'
    res_list = runner.invoke(app, ["faces", "list", "--home", str(home)])
    assert res_list.exit_code == 0
    assert "Ada Lovelace" in res_list.output
    assert "Charles Babbage" in res_list.output

    # Test CLI 'archiv who'
    res_who_p1 = runner.invoke(app, ["who", ada_cluster.label, "--home", str(home)])
    assert res_who_p1.exit_code == 0
    assert "Unconfirmed" in res_who_p1.output
    assert "Ada Lovelace" in res_who_p1.output

    # User confirms identity
    res_confirm = runner.invoke(
        app,
        ["who", ada_cluster.label, "--confirm", "Ada Lovelace", "--home", str(home)],
    )
    assert res_confirm.exit_code == 0
    norm_output = " ".join(res_confirm.output.split())
    assert "is now confirmed as 'Ada Lovelace'" in norm_output

    # Look up by confirmed name
    res_who_name = runner.invoke(
        app,
        ["who", "Ada Lovelace", "--home", str(home), "--json"],
    )
    assert res_who_name.exit_code == 0
    assert '"status": "confirmed"' in res_who_name.output
    assert '"confirmed_name": "Ada Lovelace"' in res_who_name.output

    # Explain shows citations and member detections
    res_explain = runner.invoke(
        app,
        ["who", "Ada Lovelace", "--explain", "--home", str(home)],
    )
    assert res_explain.exit_code == 0
    assert "Cluster Member Face Detections" in res_explain.output
    assert "Bounding Box" in res_explain.output

    # Revoke confirmation
    res_revoke = runner.invoke(
        app,
        ["who", "Ada Lovelace", "--revoke", "--home", str(home)],
    )
    assert res_revoke.exit_code == 0
    assert "Confirmation revoked" in res_revoke.output

    updated_attr = attribute_cluster(layout, ada_cluster.cluster_id)
    assert updated_attr is not None
    assert updated_attr.status == "unconfirmed"


def test_first_class_erasure_leaves_originals_untouched(tmp_path: Path) -> None:
    home = tmp_path / "archiv_home"
    save_face_config(FaceConfig(opt_in=True), home=home)

    img_file = tmp_path / "ada_face.png"
    _save_synthetic_image(
        img_file,
        skin_color=(210, 160, 130),
        eye_color=(20, 20, 150),
        mouth_color=(180, 70, 70),
        hair_color=(50, 30, 10),
    )
    ingestion_res = ingest_file(img_file, home=home)
    digest = ingestion_res.object_sha256

    layout = ArchivLayout.resolve(home)
    orig_path = layout.original_path(digest)
    assert orig_path.is_file(), "Original image must exist in originals/ store"

    scan_and_cluster_faces(home=home)
    status_before = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert '"total_faces": 1' in status_before.output

    # Forget all face biometrics
    res_forget = runner.invoke(app, ["faces", "forget", "--all", "--home", str(home)])
    assert res_forget.exit_code == 0
    assert "Biometric erasure complete" in res_forget.output

    # Status confirms 0 faces and 0 clusters
    status_after = runner.invoke(app, ["faces", "status", "--home", str(home), "--json"])
    assert '"total_faces": 0' in status_after.output
    assert '"total_clusters": 0' in status_after.output

    # Crucial guarantee: original image file is untouched
    assert orig_path.is_file(), "First-class erasure MUST NOT touch original files"
    assert orig_path.read_bytes() == img_file.read_bytes()
