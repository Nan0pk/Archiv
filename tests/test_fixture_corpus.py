from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path
from typing import cast
from zipfile import BadZipFile, ZipFile, is_zipfile

import pytest

DESCRIPTORS = Path(__file__).parent / "fixtures" / "representative-corpus"
GENERATOR = Path(__file__).parents[1] / "scripts" / "generate_fixture_corpus.py"


def _json_object(path: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    output = tmp_path / "corpus"
    subprocess.run(
        [sys.executable, str(GENERATOR), "--output", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    return output


def test_generator_matches_committed_descriptors(corpus: Path) -> None:
    assert (corpus / "manifest.json").read_bytes() == (DESCRIPTORS / "manifest.json").read_bytes()
    assert (corpus / "expected.json").read_bytes() == (DESCRIPTORS / "expected.json").read_bytes()
    assert (corpus / "PROVENANCE.md").read_bytes() == (DESCRIPTORS / "PROVENANCE.md").read_bytes()


def test_manifest_matches_generated_bytes(corpus: Path) -> None:
    manifest = _json_object(DESCRIPTORS / "manifest.json")
    assert manifest["schema_version"] == 1
    entries = cast(list[dict[str, object]], manifest["entries"])
    assert len(entries) == 15

    for entry in entries:
        relative_path = cast(str, entry["path"])
        content = (corpus / relative_path).read_bytes()
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]


def test_expected_locations_and_markers_are_declared() -> None:
    expected = _json_object(DESCRIPTORS / "expected.json")
    fixtures = cast(dict[str, dict[str, object]], expected["fixtures"])
    assert fixtures["plain-text.txt"]["location"] == {"line": 3}
    assert fixtures["report.pdf"]["location"] == {"page": 1}
    assert fixtures["document.docx"]["location"] == {"paragraph": 2}
    assert fixtures["workbook.xlsx"]["location"] == {
        "cell": "B2",
        "sheet": "Evidence",
    }
    assert fixtures["presentation.pptx"]["location"] == {
        "shape": 2,
        "slide": 1,
    }
    assert fixtures["sample.wav"]["marker"] == "ARCHIV-AUDIO-MARKER-2026"
    assert fixtures["operations.txt"]["location"] == {"line": 2}
    assert fixtures["research.md"]["location"] == {"line": 3}
    assert fixtures["decision.txt"]["location"] == {"line": 2}


def test_office_packages_are_valid_zip_containers_with_markers(corpus: Path) -> None:
    cases = {
        "document.docx": ("word/document.xml", b"ARCHIV-DOCX-MARKER-2026"),
        "workbook.xlsx": (
            "xl/worksheets/sheet1.xml",
            b"ARCHIV-XLSX-MARKER-2026",
        ),
        "presentation.pptx": (
            "ppt/slides/slide1.xml",
            b"ARCHIV-PPTX-MARKER-2026",
        ),
    }
    for filename, (member, marker) in cases.items():
        path = corpus / filename
        assert is_zipfile(path)
        with ZipFile(path) as archive:
            assert "[Content_Types].xml" in archive.namelist()
            assert "_rels/.rels" in archive.namelist()
            assert marker in archive.read(member)
            assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_pdf_png_and_wav_have_expected_structure(corpus: Path) -> None:
    pdf = (corpus / "report.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-1.4")
    assert b"ARCHIV-PDF-MARKER-2026" in pdf
    assert pdf.endswith(b"%%EOF\n")

    png = (corpus / "scanned-page.png").read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (640, 80)

    wav_path = corpus / "sample.wav"
    wav_bytes = wav_path.read_bytes()
    assert b"ARCHIV-AUDIO-MARKER-2026" in wav_bytes
    with wave.open(str(wav_path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 8000
        assert audio.getnframes() == 6800


def test_malformed_samples_are_detectably_invalid(corpus: Path) -> None:
    truncated_pdf = (corpus / "malformed/truncated.pdf").read_bytes()
    assert truncated_pdf.startswith(b"%PDF-")
    assert b"xref" not in truncated_pdf
    assert b"%%EOF" not in truncated_pdf

    fake_docx = corpus / "malformed/not-a-docx.docx"
    assert not is_zipfile(fake_docx)
    with pytest.raises(BadZipFile):
        ZipFile(fake_docx)

    corrupt_wav = (corpus / "malformed/corrupt.wav").read_bytes()
    assert corrupt_wav.startswith(b"RIFF")
    assert corrupt_wav[8:12] != b"WAVE"
