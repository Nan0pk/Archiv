"""Human-facing bounded source-location command."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from archiv.source_location import (
    load_citation_file,
    resolve_citation_location,
    resolve_object_location,
)


def _locator_text(locator: dict[str, object]) -> str:
    return ", ".join(f"{key} {value}" for key, value in sorted(locator.items())) or "source"


def register_source_location_command(app: typer.Typer) -> Callable[..., None]:
    """Attach the bounded, read-only source-location command."""

    @app.command("source")
    def source_command(
        object_sha256: Annotated[
            str | None,
            typer.Argument(help="Lowercase SHA-256 object digest."),
        ] = None,
        citation_file: Annotated[
            Path | None,
            typer.Option(
                "--citation-file",
                exists=True,
                file_okay=True,
                dir_okay=False,
                readable=True,
                resolve_path=True,
                help="JSON citation, find output, ask result, or report manifest.",
            ),
        ] = None,
        citation_number: Annotated[
            int,
            typer.Option("--citation-number", min=1, help="One-based citation selection."),
        ] = 1,
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, resolve_path=True),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Locate one independently verified preserved source without opening it."""

        if (object_sha256 is None) == (citation_file is None):
            typer.echo(
                "source failed: provide exactly one object digest or --citation-file",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            if citation_file is not None:
                citation = load_citation_file(citation_file, citation_number=citation_number)
                location = resolve_citation_location(citation, home=home)
            else:
                assert object_sha256 is not None
                location = resolve_object_location(object_sha256, home=home)
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"source failed: {error}", err=True)
            raise typer.Exit(code=1) from error

        if json_output:
            typer.echo(json.dumps(location.model_dump(mode="json"), indent=2, sort_keys=True))
            return

        typer.echo(f"Source: {location.source_name}")
        typer.echo(f"Type: {location.media_type} ({location.kind})")
        typer.echo(f"Object: {location.object_sha256}")
        typer.echo(
            "Location: "
            + (_locator_text(location.locator) if location.locator is not None else "whole source")
        )
        typer.echo(f"Preserved file: {location.canonical_path}")
        validation_suffix = " and citation" if location.citation_validated else ""
        typer.echo(f"Validated: immutable original{validation_suffix}")
        typer.echo("Mode: read-only")

    return source_command
