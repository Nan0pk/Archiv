"""Encoding-aware declaration checks for XML read from local documents."""

from __future__ import annotations

from xml.parsers import expat


class XmlDeclarationError(ValueError):
    """A DTD or entity declaration was encountered before any expansion."""


class MalformedXmlError(ValueError):
    """The input is not well-formed XML."""


def _refuse_declaration(*_: object) -> None:
    raise XmlDeclarationError("XML declarations and entities are forbidden")


def refuse_xml_declarations(data: bytes) -> None:
    """Check XML without building a tree or resolving external resources.

    Raw-byte searches miss UTF-16 declarations. Expat decodes the input and stops
    at the start of a DOCTYPE, before reading its subset or expanding an entity.
    XML encoding declarations, predefined entities, comments and CDATA remain valid.
    Callers retain their own size, node and format-specific limits.
    """

    scanner = expat.ParserCreate()
    scanner.StartDoctypeDeclHandler = _refuse_declaration
    scanner.EntityDeclHandler = _refuse_declaration
    try:
        scanner.Parse(data, True)
    except expat.ExpatError as error:
        raise MalformedXmlError("XML is not well-formed") from error
