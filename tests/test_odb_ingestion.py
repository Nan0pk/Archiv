from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest

import archiv.ingestion.normalize_odb as odb
from archiv.hashing import sha256_file
from archiv.ingestion import ingest_file, rebuild_derived
from archiv.ingestion.normalizers import MalformedInputError, normalize

DIGEST = "0" * 64
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
DATABASE = "urn:oasis:names:tc:opendocument:xmlns:database:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
XLINK = "http://www.w3.org/1999/xlink"
MIMETYPE = "application/vnd.oasis.opendocument.base"
OPAQUE_PAYLOAD = b"opaque-firebird-payload-0123456789"


def _manifest(mimetype: str) -> str:
    entries = [
        ("/", mimetype),
        ("content.xml", "text/xml"),
        ("database/firebird.fdb", "application/octet-stream"),
        ("forms/Obj1/content.xml", "text/xml"),
        ("reports/Obj2/content.xml", "text/xml"),
    ]
    rendered = "".join(
        f'<manifest:file-entry manifest:full-path="{path}" manifest:media-type="{media_type}"/>'
        for path, media_type in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<manifest:manifest xmlns:manifest="{MANIFEST}">{rendered}</manifest:manifest>'
    )


def _content(*, body: str | None = None, names: list[str] | None = None) -> str:
    if body is None:
        table_names = names or ["Clients", "Invoices"]
        table_representations = "".join(
            f'<db:table-representation db:name="{name}"/>' for name in table_names
        )
        body = (
            "<office:database>"
            "<db:data-source><db:connection-data>"
            '<db:connection-resource xlink:href="sdbc:embedded:firebird"/>'
            '<db:login db:user-name="admin-secret"/>'
            "</db:connection-data></db:data-source>"
            '<db:forms><db:component db:name="Client Form" xlink:href="forms/Obj1"/>'
            "</db:forms>"
            '<db:reports><db:component db:name="Client Report" xlink:href="reports/Obj2"/>'
            "</db:reports>"
            '<db:queries><db:query db:name="Active Clients" '
            'db:command="SELECT secret_token FROM Clients"/></db:queries>'
            f"<db:table-representations>{table_representations}</db:table-representations>"
            "<db:schema-definition><db:table-definitions>"
            '<db:table-definition db:name="Clients"/>'
            '<db:table-definition db:name="Invoices"/>'
            "</db:table-definitions></db:schema-definition>"
            "</office:database>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content xmlns:office="{OFFICE}" xmlns:db="{DATABASE}" '
        f'xmlns:xlink="{XLINK}"><office:body>{body}</office:body>'
        "</office:document-content>"
    )


def _package(
    path: Path,
    *,
    mimetype: str = MIMETYPE,
    content: str | None = None,
) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", mimetype, compress_type=ZIP_STORED)
        archive.writestr("content.xml", content or _content(), compress_type=ZIP_DEFLATED)
        archive.writestr(
            "database/firebird.fdb",
            OPAQUE_PAYLOAD,
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "forms/Obj1/content.xml",
            "<form-document/>",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "reports/Obj2/content.xml",
            "<report-document/>",
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "META-INF/manifest.xml",
            _manifest(mimetype),
            compress_type=ZIP_DEFLATED,
        )


def test_odb_exposes_only_bounded_object_metadata(tmp_path: Path) -> None:
    path = tmp_path / "customers.odb"
    _package(path)

    result = normalize(path, DIGEST)

    assert result.kind == "odb"
    assert result.media_type == MIMETYPE
    assert [segment.text for segment in result.segments] == [
        "Database table: Clients",
        "Database table: Invoices",
        "Database query: Active Clients",
        "Database form: Client Form",
        "Database report: Client Report",
    ]
    assert result.metadata["database_policy"] == "metadata-only"
    assert result.metadata["database_object_counts"] == {
        "tables": 2,
        "queries": 1,
        "forms": 1,
        "reports": 1,
    }
    assert result.metadata["connection_descriptor_count"] == 3
    assert result.metadata["query_command_count"] == 1
    assert result.metadata["external_links_ignored"] == 1
    assert result.metadata["opaque_database_member_count"] == 1
    assert result.metadata["opaque_database_member_bytes"] == len(OPAQUE_PAYLOAD)
    assert result.metadata["connections_opened"] is False
    assert result.metadata["queries_executed"] is False
    assert result.metadata["embedded_payloads_opened"] is False
    assert result.tables == []

    serialized = result.model_dump_json()
    for forbidden in (
        "secret_token",
        "admin-secret",
        "sdbc:embedded:firebird",
        "database/firebird.fdb",
        "forms/Obj1",
        "reports/Obj2",
    ):
        assert forbidden not in serialized


def test_odb_preserves_urdu_object_names(tmp_path: Path) -> None:
    path = tmp_path / "urdu-database.odb"
    _package(path, content=_content(names=["صارفین", "رپورٹس"]))

    result = normalize(path, DIGEST)

    assert [segment.text for segment in result.segments[:3]] == [
        "Database table: صارفین",
        "Database table: رپورٹس",
        "Database table: Clients",
    ]


def test_odb_rejects_deprecated_database_mimetype(tmp_path: Path) -> None:
    path = tmp_path / "legacy.odb"
    _package(path, mimetype="application/vnd.oasis.opendocument.database")

    with pytest.raises(MalformedInputError, match="mimetype mismatch"):
        normalize(path, DIGEST)


def test_odb_requires_database_body(tmp_path: Path) -> None:
    path = tmp_path / "substituted.odb"
    _package(path, content=_content(body="<office:text/>"))

    with pytest.raises(MalformedInputError, match="exactly one office:database"):
        normalize(path, DIGEST)


def test_odb_rejects_excessive_object_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(odb, "MAX_DATABASE_OBJECTS", 1)
    path = tmp_path / "too-many.odb"
    _package(path, content=_content(names=["One", "Two"]))

    with pytest.raises(MalformedInputError, match="database object limit exceeded"):
        normalize(path, DIGEST)


def test_odb_rejects_excessive_object_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(odb, "MAX_DATABASE_NAME_CHARACTERS", 4)
    path = tmp_path / "long-name.odb"
    _package(path, content=_content(names=["Long Name"]))

    with pytest.raises(MalformedInputError, match="object name character limit exceeded"):
        normalize(path, DIGEST)


def test_odb_complete_immutable_ingestion_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "lifecycle.odb"
    _package(source)
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
    assert payload["kind"] == "odb"
    assert payload["metadata"]["queries_executed"] is False

    renamed = tmp_path / "renamed.odb"
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
