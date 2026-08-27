"""Lawful synthetic regression cases for hostile parser/container boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from archiv.ingestion.limits import LimitExceededError, check_input, check_zip
from archiv.ingestion.normalizers import MalformedInputError, normalize


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_zip_rejects_traversal_and_backup_recursion(tmp_path: Path) -> None:
    traversal = _zip(tmp_path / "bad.zip", {"../../original": b"changed"})
    with pytest.raises(LimitExceededError, match="unsafe member path"):
        check_zip(traversal)

    nested = "/".join(["backup"] * 9) + "/data"
    with pytest.raises(LimitExceededError, match="recursion"):
        check_zip(_zip(tmp_path / "nested.zip", {nested: b"synthetic"}))


@pytest.mark.parametrize("suffix", ["docx", "xlsx", "pptx", "odt", "ods", "odp", "odb"])
def test_malformed_office_and_odf_packages_fail_visibly(tmp_path: Path, suffix: str) -> None:
    path = _zip(tmp_path / f"malformed.{suffix}", {"unexpected.xml": b"<broken>"})
    with pytest.raises(MalformedInputError):
        normalize(path, "0" * 64)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("broken.pdf", b"%PDF-1.7\nsynthetic truncated"),
        ("broken.inp", b"synthetic, not a CFB container"),
        ("broken.odb", b"SQLite format 3\x00synthetic"),
    ],
)
def test_malformed_parser_inputs_fail_visibly(tmp_path: Path, name: str, payload: bytes) -> None:
    path = tmp_path / name
    path.write_bytes(payload)
    with pytest.raises(MalformedInputError):
        normalize(path, "0" * 64)


def test_image_pixel_limit_is_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synthetic.png"
    Image.new("RGB", (2, 2)).save(path)
    monkeypatch.setattr("archiv.ingestion.limits.MAX_IMAGE_PIXELS", 3)
    with pytest.raises(MalformedInputError, match="image pixels limit exceeded"):
        normalize(path, "0" * 64)


def test_source_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("synthetic", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(LimitExceededError, match="symbolic-link"):
        check_input(link)


def test_citation_envelope_fixture_is_synthetic_and_inert(tmp_path: Path) -> None:
    fixture = tmp_path / "citation-envelope.json"
    fixture.write_text(json.dumps({"citation": "{{ template }}", "path": "../../original"}))
    decoded = json.loads(fixture.read_text())
    assert decoded["citation"] == "{{ template }}"
    assert decoded["path"] == "../../original"
