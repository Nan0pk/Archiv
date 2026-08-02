"""Bounded, metadata-only inspection for OpenDocument database front ends."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unicodedata import category
from xml.etree import ElementTree
from zipfile import ZipFile

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.ingestion.normalize_odf import (
    OFFICE,
    XLINK,
    parse_odf_xml,
    read_odf_package,
)

ODB_MIMETYPE = "application/vnd.oasis.opendocument.base"
DATABASE = "urn:oasis:names:tc:opendocument:xmlns:database:1.0"
MAX_DATABASE_OBJECTS = 10_000
MAX_DATABASE_NAME_CHARACTERS = 1_024


def _validated_database(root: ElementTree.Element) -> ElementTree.Element:
    if root.tag != f"{{{OFFICE}}}document-content":
        raise ValueError("ODB content.xml has an unexpected root element")
    body = root.find(f"{{{OFFICE}}}body")
    if body is None:
        raise ValueError("ODB content.xml has no office:body")
    children = list(body)
    if len(children) != 1 or children[0].tag != f"{{{OFFICE}}}database":
        raise ValueError("ODB content body does not contain exactly one office:database")
    return children[0]


def _bounded_name(element: ElementTree.Element) -> str | None:
    raw = element.attrib.get(f"{{{DATABASE}}}name")
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > MAX_DATABASE_NAME_CHARACTERS:
        raise ValueError("ODB object name character limit exceeded")
    if any(category(character) == "Cc" for character in value):
        raise ValueError("ODB object name contains control characters")
    return value


def _named_elements(
    container: ElementTree.Element | None,
    element_name: str,
) -> list[str]:
    if container is None:
        return []
    names: list[str] = []
    for element in container.iter(f"{{{DATABASE}}}{element_name}"):
        name = _bounded_name(element)
        if name is not None:
            names.append(name)
    return names


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _database_objects(database: ElementTree.Element) -> dict[str, list[str]]:
    table_representations = database.find(f"{{{DATABASE}}}table-representations")
    schema_definition = database.find(f"{{{DATABASE}}}schema-definition")
    queries = database.find(f"{{{DATABASE}}}queries")
    forms = database.find(f"{{{DATABASE}}}forms")
    reports = database.find(f"{{{DATABASE}}}reports")

    table_names = _named_elements(table_representations, "table-representation")
    table_names.extend(_named_elements(schema_definition, "table-definition"))
    objects = {
        "table": _unique(table_names),
        "query": _named_elements(queries, "query"),
        "form": _named_elements(forms, "component"),
        "report": _named_elements(reports, "component"),
    }
    if sum(len(names) for names in objects.values()) > MAX_DATABASE_OBJECTS:
        raise ValueError("ODB database object limit exceeded")
    return objects


def _object_segments(objects: dict[str, list[str]]) -> list[NormalizedSegment]:
    segments: list[NormalizedSegment] = []
    for object_kind in ("table", "query", "form", "report"):
        for index, name in enumerate(objects[object_kind], 1):
            segments.append(
                NormalizedSegment(
                    locator={
                        "database_object": object_kind,
                        "index": index,
                        "name": name,
                    },
                    text=f"Database {object_kind}: {name}",
                )
            )
    return segments


def _opaque_database_members(path: Path) -> tuple[int, int]:
    count = 0
    size = 0
    with ZipFile(BytesIO(path.read_bytes())) as archive:
        for entry in archive.infolist():
            if not entry.is_dir() and entry.filename.lower().startswith("database/"):
                count += 1
                size += entry.file_size
    return count, size


def _count_external_links(root: ElementTree.Element) -> int:
    count = 0
    for element in root.iter():
        href = element.attrib.get(f"{{{XLINK}}}href")
        if href and (href.startswith("//") or ":" in href.split("/", 1)[0]):
            count += 1
    return count


def normalize_odb(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
) -> NormalizedDocument:
    """Inspect an ODB package without opening connections or database payloads."""

    if media_type != ODB_MIMETYPE:
        raise ValueError("ODB registry media type mismatch")
    package = read_odf_package(path, ODB_MIMETYPE)
    root = parse_odf_xml(package.content, label="ODB content XML")
    database = _validated_database(root)
    objects = _database_objects(database)
    segments = _object_segments(objects)

    connection_tags = {
        f"{{{DATABASE}}}connection-data",
        f"{{{DATABASE}}}database-description",
        f"{{{DATABASE}}}file-based-database",
        f"{{{DATABASE}}}server-database",
        f"{{{DATABASE}}}connection-resource",
        f"{{{DATABASE}}}login",
    }
    connection_descriptor_count = sum(
        1 for element in database.iter() if element.tag in connection_tags
    )
    query_command_count = sum(
        1
        for element in database.iter(f"{{{DATABASE}}}query")
        if f"{{{DATABASE}}}command" in element.attrib
    )
    opaque_member_count, opaque_member_bytes = _opaque_database_members(path)

    object_counts = {
        "tables": len(objects["table"]),
        "queries": len(objects["query"]),
        "forms": len(objects["form"]),
        "reports": len(objects["report"]),
    }
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="odb",
        source_name=source_name,
        segments=segments,
        metadata={
            "processor": "archiv.odb-metadata",
            "processor_version": "1",
            "package_mimetype": package.mimetype,
            "package_manifest_validated": True,
            "database_policy": "metadata-only",
            "database_object_counts": object_counts,
            "connection_descriptor_count": connection_descriptor_count,
            "query_command_count": query_command_count,
            "external_links_ignored": _count_external_links(root),
            "opaque_database_member_count": opaque_member_count,
            "opaque_database_member_bytes": opaque_member_bytes,
            "connections_opened": False,
            "queries_executed": False,
            "query_commands_retained": False,
            "connection_values_retained": False,
            "embedded_payloads_opened": False,
            "database_member_paths_retained": False,
            "component_documents_parsed": False,
            "macros_executed": False,
        },
    )
