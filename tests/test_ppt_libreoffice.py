"""A .ppt our reader rejects as malformed is read by sandboxed LibreOffice, or stays rejected."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

import olefile
import pytest
from pptx import Presentation
from pptx.util import Inches

from archiv.format_matrix import load_format_matrix
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file, ppt_libreoffice, rebuild_derived
from archiv.ingestion.normalize_legacy_office import normalize_ppt
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
TITLE = "Harbour ledger review"
BODY = "Quarterly tally of pier fees"


def _sandbox_works() -> bool:
    office = shutil.which("soffice") or shutil.which("libreoffice")
    bubblewrap = shutil.which("bwrap")
    if office is None or bubblewrap is None:
        return False
    probe = subprocess.run(
        [bubblewrap, "--unshare-net", "--ro-bind", "/", "/", "--dev", "/dev", "--", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


needs_sandbox = pytest.mark.skipif(
    not _sandbox_works(), reason="LibreOffice or a working bubblewrap is not available"
)


def _write_damaged_ppt(tmp_path: Path) -> Path:
    """LibreOffice writes a .ppt; then one record's length grows past its container's end."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    title = slide.shapes.title
    assert title is not None
    title.text = TITLE
    slide.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(1)).text_frame.text = BODY
    pptx = tmp_path / "made.pptx"
    prs.save(str(pptx))
    office = shutil.which("soffice") or shutil.which("libreoffice")
    assert office is not None
    environment = dict(os.environ, HOME=str(tmp_path), GSETTINGS_BACKEND="memory")
    subprocess.run(
        [
            office,
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{(tmp_path / 'profile').as_posix()}",
            "--convert-to",
            "ppt",
            "--outdir",
            str(tmp_path),
            str(pptx),
        ],
        env=environment,
        capture_output=True,
        check=True,
        timeout=120,
    )
    path = tmp_path / "made.ppt"
    ole = olefile.OleFileIO(str(path))
    data = bytearray(ole.openstream("PowerPoint Document").read())
    ole.close()

    def last_atoms(start: int, end: int, depth: int) -> list[int]:
        found: list[int] = []
        offset = start
        while offset + 8 <= end:
            ver_instance, rec_type, length = struct.unpack_from("<HHI", data, offset)
            container = ver_instance & 0xF == 0xF
            if container and offset + 8 + length <= end:
                found += last_atoms(offset + 8, offset + 8 + length, depth + 1)
            elif depth >= 2 and offset + 8 + length == end and rec_type not in (0x0FA0, 0x0FA8):
                found.append(offset)
            offset += 8 + length
        return found

    target = last_atoms(0, len(data), 0)[0]
    struct.pack_into("<I", data, target + 4, struct.unpack_from("<I", data, target + 4)[0] + 4)
    ole = olefile.OleFileIO(str(path), write_mode=True)
    ole.write_stream("PowerPoint Document", bytes(data))
    ole.close()
    return path


@needs_sandbox
def test_damaged_ppt_is_read_through_libreoffice_and_says_so(tmp_path: Path) -> None:
    path = _write_damaged_ppt(tmp_path)
    with pytest.raises(ValueError, match=ppt_libreoffice.FALLBACK_REASON):
        normalize_ppt(
            path, DIGEST, source_name="made.ppt", media_type="application/vnd.ms-powerpoint"
        )

    document = normalize(path, DIGEST)

    texts = [segment.text for segment in document.segments]
    assert TITLE in texts and BODY in texts
    assert document.kind == "ppt"
    assert all(s.locator["origin"] == ppt_libreoffice.ORIGIN for s in document.segments)
    assert document.metadata["converted_because"] == ppt_libreoffice.FALLBACK_REASON
    matrix = load_format_matrix(Path(__file__).parents[1] / "docs" / "format-compatibility.json")
    claimed = {frozenset(shape) for shape in matrix.family_for_suffix(".ppt").locator_shapes}
    assert all(frozenset(s.locator) in claimed for s in document.segments)


@needs_sandbox
def test_ingesting_a_damaged_ppt_keeps_the_original_and_labels_the_text(tmp_path: Path) -> None:
    source = _write_damaged_ppt(tmp_path)
    before = source.read_bytes()

    result = ingest_file(source, home=tmp_path / "home")

    assert result.status == "succeeded"
    assert source.read_bytes() == before
    assert Path(result.original_path).read_bytes() == before
    assert sha256_file(Path(result.original_path)) == result.object_sha256
    payload = json.loads(
        (Path(result.derived_root) / "normalized" / "document.json").read_text(encoding="utf-8")
    )
    assert payload["kind"] == "ppt"
    assert {s["locator"]["origin"] for s in payload["segments"]} == {ppt_libreoffice.ORIGIN}
    assert payload["metadata"]["libreoffice_version"].startswith("LibreOffice")

    normalized = Path(result.derived_root) / "normalized" / "document.json"
    before_rebuild = sha256_file(normalized)
    shutil.rmtree(result.derived_root)
    assert rebuild_derived(result.object_sha256, home=tmp_path / "home")
    assert sha256_file(normalized) == before_rebuild


@needs_sandbox
def test_the_converted_file_is_held_to_the_input_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_damaged_ppt(tmp_path)
    checked: list[str] = []

    def refuse(converted: Path) -> None:
        checked.append(converted.name)
        raise ValueError("expansion limit exceeded")

    monkeypatch.setattr(ppt_libreoffice, "check_input", refuse)
    with pytest.raises(MalformedInputError, match="expansion limit"):
        normalize(path, DIGEST)
    assert checked == ["in.pptx"]


def test_without_the_tools_the_original_rejection_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ppt_libreoffice, "_tools", lambda: None)
    rejection = ValueError(ppt_libreoffice.FALLBACK_REASON)
    with pytest.raises(ValueError, match="bubblewrap") as caught:
        ppt_libreoffice.convert_rejected_ppt(
            tmp_path / "missing.ppt",
            DIGEST,
            source_name="x.ppt",
            media_type="x",
            rejection=rejection,
        )
    assert ppt_libreoffice.FALLBACK_REASON in str(caught.value)


@needs_sandbox
def test_a_failed_conversion_keeps_the_rejection(tmp_path: Path) -> None:
    path = tmp_path / "noise.ppt"
    path.write_bytes(b"\x00\xff" * 4096)
    with pytest.raises(ValueError, match="could not convert") as caught:
        ppt_libreoffice.convert_rejected_ppt(
            path,
            DIGEST,
            source_name="noise.ppt",
            media_type="application/vnd.ms-powerpoint",
            rejection=ValueError(ppt_libreoffice.FALLBACK_REASON),
        )
    assert ppt_libreoffice.FALLBACK_REASON in str(caught.value)


@needs_sandbox
def test_a_conversion_past_its_time_limit_keeps_the_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_damaged_ppt(tmp_path)
    monkeypatch.setattr(ppt_libreoffice, "LIBREOFFICE_WALL_SECONDS", 0.01)
    with pytest.raises(MalformedInputError, match="did not finish"):
        normalize(path, DIGEST)


def test_other_ppt_rejections_never_reach_libreoffice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("LibreOffice must not be tried")

    monkeypatch.setattr("archiv.ingestion.extractors.convert_rejected_ppt", refuse)
    path = tmp_path / "corrupt.ppt"
    path.write_bytes(b"not an OLE2 container, just noise 0123456789")
    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)
