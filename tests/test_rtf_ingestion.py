"""Legacy RTF ingestion: extraction, Unicode/hex escapes, and fail-closed safety.

Fixtures are hand-authored RTF text (RTF's body is 7-bit ASCII control words),
lawful and self-authored, mirroring this repo's existing convention of
generating fixtures from constants rather than sourcing real documents
(see tests/fixtures/README.md).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file, rebuild_derived
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
MARKER = "ARCHIV-RTF-MARKER"
MEDIA_TYPE = "application/rtf"


def _rtf(body: str) -> bytes:
    return (r"{\rtf1\ansi\ansicpg1252\deff0" + body + "}").encode("ascii")


def _write_rtf(path: Path, *, body: str = rf"Archiv RTF fixture {MARKER}\par") -> None:
    path.write_bytes(_rtf(body))


def test_rtf_extracts_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "document.rtf"
    _write_rtf(path)

    document = normalize(path, DIGEST)

    assert document.kind == "rtf"
    assert document.media_type == MEDIA_TYPE
    assert len(document.segments) == 1
    assert document.segments[0].locator == {"paragraph": 1}
    assert document.segments[0].text == f"Archiv RTF fixture {MARKER}"
    assert document.metadata["macros_executed"] is False
    assert document.metadata["processor"] == "archiv.legacy-rtf"


def test_rtf_splits_multiple_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "multi.rtf"
    _write_rtf(path, body=rf"First paragraph\par Second {MARKER}\par")

    document = normalize(path, DIGEST)

    assert [(s.locator, s.text) for s in document.segments] == [
        ({"paragraph": 1}, "First paragraph"),
        ({"paragraph": 2}, f"Second {MARKER}"),
    ]


def test_rtf_decodes_hex_escape_using_ansicpg(tmp_path: Path) -> None:
    # 0x93/0x94 are the CP1252 curly-quote positions.
    path = tmp_path / "hex-escape.rtf"
    _write_rtf(path, body=rf"\'93quoted {MARKER}\'94\par")

    document = normalize(path, DIGEST)

    assert document.segments[0].text == f"“quoted {MARKER}”"


def test_rtf_decodes_unicode_escape_and_skips_ascii_fallback(tmp_path: Path) -> None:
    # \u8226 is U+2022 (bullet); \'95 is its single-byte CP1252 fallback that a
    # \uc1-scoped reader must skip rather than emit as extra text.
    path = tmp_path / "unicode-escape.rtf"
    _write_rtf(path, body=rf"\uc1\u8226 \'95 {MARKER}\par")

    document = normalize(path, DIGEST)

    assert document.segments[0].text == f"• {MARKER}"


def test_rtf_skips_font_table_and_document_info(tmp_path: Path) -> None:
    path = tmp_path / "with-tables.rtf"
    body = (
        r"{\fonttbl{\f0 Calibri;}}"
        r"{\info{\title Secret Title}{\author Secret Author}}"
        rf"\f0 {MARKER}\par"
    )
    _write_rtf(path, body=body)

    document = normalize(path, DIGEST)

    texts = {segment.text for segment in document.segments}
    assert texts == {MARKER}
    serialized = document.model_dump_json()
    assert "Secret Title" not in serialized
    assert "Secret Author" not in serialized
    assert "Calibri" not in serialized


def test_rtf_never_opens_embedded_object_payload(tmp_path: Path) -> None:
    path = tmp_path / "with-object.rtf"
    # \bin advances past raw binary that could otherwise be misread as control
    # structure; the whole \object destination (including \objdata) is discarded.
    payload = "MACRO-PAYLOAD-SHOULD-NEVER-BE-READ-OR-EXECUTED"
    body = (
        r"{\object\objclass Package"
        r"{\objdata \bin" + str(len(payload)) + " " + payload + "}"
        "}"
        rf"\par {MARKER}\par"
    )
    _write_rtf(path, body=body)

    document = normalize(path, DIGEST)

    texts = {segment.text for segment in document.segments}
    assert texts == {MARKER}
    serialized = document.model_dump_json()
    assert "MACRO-PAYLOAD" not in serialized
    assert document.metadata["macros_executed"] is False
    assert document.metadata["embedded_objects_opened"] is False


def test_rtf_rejects_missing_magic(tmp_path: Path) -> None:
    path = tmp_path / "not-rtf.rtf"
    path.write_bytes(b"not an RTF file, just noise 0123456789")

    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)


def test_rtf_rejects_unmatched_braces(tmp_path: Path) -> None:
    path = tmp_path / "unbalanced.rtf"
    path.write_bytes(rb"{\rtf1\ansi " + MARKER.encode("ascii"))

    with pytest.raises(MalformedInputError):
        normalize(path, DIGEST)


def test_rtf_complete_immutable_ingestion_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "lifecycle.rtf"
    _write_rtf(source)
    source_before = source.read_bytes()
    home = tmp_path / "archiv-home"

    first = ingest_file(source, home=home)
    assert first.status == "succeeded"
    assert first.source_hash_unchanged is True
    assert source.read_bytes() == source_before
    original = Path(first.original_path)
    original_hash = sha256_file(original)
    normalized = Path(first.derived_root) / "normalized" / "document.json"
    normalized_hash = sha256_file(normalized)
    payload = json.loads(normalized.read_text(encoding="utf-8"))
    assert payload["kind"] == "rtf"
    assert payload["metadata"]["macros_executed"] is False

    renamed = tmp_path / "renamed.rtf"
    renamed.write_bytes(source_before)
    second = ingest_file(renamed, home=home)
    assert second.duplicate is True
    assert second.object_sha256 == first.object_sha256
    assert second.original_path == first.original_path

    shutil.rmtree(first.derived_root)
    evidence = rebuild_derived(first.object_sha256, home=home)
    assert evidence
    assert sha256_file(original) == original_hash
    assert sha256_file(normalized) == normalized_hash
