from __future__ import annotations

import json
from pathlib import Path

import pytest

from archiv.ingestion import ingest_file
from archiv.ingestion.normalizers import MalformedInputError, UnsupportedFormatError


def test_image_and_audio_record_unrun_processors_honestly(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARCHIV_OCR", "off")
    home = tmp_path / "archiv-home"
    image = ingest_file(ingestion_corpus / "scanned-page.png", home=home)
    audio = ingest_file(ingestion_corpus / "sample.wav", home=home)

    image_ocr = json.loads(
        (Path(image.derived_root) / "ocr" / "status.json").read_text(encoding="utf-8")
    )
    audio_transcript = json.loads(
        (Path(audio.derived_root) / "transcripts" / "status.json").read_text(encoding="utf-8")
    )
    assert image_ocr["status"] == "skipped"
    assert image_ocr["reason"] == "OCR disabled by ARCHIV_OCR"
    assert audio_transcript["status"] == "not_run"
    assert any(
        item.processor == "archiv.visual-ocr" and item.status == "skipped"
        for item in image.processing
    )
    assert any(
        item.processor == "archiv.transcription" and item.status == "skipped"
        for item in audio.processing
    )


@pytest.mark.parametrize(
    "filename",
    [
        "malformed/truncated.pdf",
        "malformed/not-a-docx.docx",
        "malformed/corrupt.wav",
    ],
)
def test_malformed_input_fails_before_archive_creation(
    ingestion_corpus: Path,
    tmp_path: Path,
    filename: str,
) -> None:
    home = tmp_path / "archiv-home"
    with pytest.raises(MalformedInputError):
        ingest_file(ingestion_corpus / filename, home=home)
    assert not home.exists()


def test_unsupported_input_fails_explicitly(tmp_path: Path) -> None:
    source = tmp_path / "unsupported.bin"
    source.write_bytes(b"not supported")
    home = tmp_path / "archiv-home"
    with pytest.raises(UnsupportedFormatError, match="unsupported file type"):
        ingest_file(source, home=home)
    assert not home.exists()


def test_default_home_is_outside_current_repository(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_home = tmp_path / "user-home"
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    monkeypatch.chdir(repository)

    result = ingest_file(ingestion_corpus / "plain-text.txt")

    expected_root = fake_home / ".local" / "share" / "archiv"
    assert Path(result.original_path).is_relative_to(expected_root)
    assert not Path(result.original_path).is_relative_to(repository)
