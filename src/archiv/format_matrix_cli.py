"""Human-facing view of the tested format-compatibility matrix (issue #37).

The command is a read-only reporter over ``docs/format-compatibility.json``: it
reads no user data, opens no sources, and never touches the Archiv home.  Every
claim it prints is re-verified against live ingestion runs by the acceptance
suite, so the command cannot promise more than the product actually does.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from archiv.format_matrix import (
    FormatFamily,
    FormatMatrix,
    load_format_matrix,
    matrix_path,
)

_EXTRACTION_TEXT = {
    "native": "native text extraction",
    "visual_ocr_conditional": "visual OCR only when explicitly enabled",
    "metadata_only": "metadata only; no document text",
}


def _normalize_suffix(value: str) -> str:
    """Accept ``pdf``, ``.pdf``, ``PDF``, or a filename and return ``.pdf``."""

    text = value.strip().lower()
    if not text:
        return ""
    suffix = Path(text).suffix or text
    return suffix if suffix.startswith(".") else f".{suffix}"


def _family_lines(family: FormatFamily) -> list[str]:
    """Readable, non-overstated detail for one family."""

    lines = [
        f"Family: {family.family}",
        f"  Support: {family.support_level}",
        f"  Suffixes: {' '.join(family.suffixes)}",
        f"  Media types: {', '.join(family.media_types)}",
        f"  Detection: {family.detection}",
        f"  Originals preserved unchanged: {'yes' if family.immutable_ingestion else 'no'}",
        f"  Text: {_EXTRACTION_TEXT.get(family.text_extraction, family.text_extraction)}",
    ]
    if family.structure:
        lines.append(f"  Structure: {', '.join(family.structure)}")
    if family.locator_shapes:
        shapes = "; ".join("/".join(shape) for shape in family.locator_shapes)
        lines.append(f"  Citation locators: {shapes}")
    lines.append(
        f"  Grounded answers and citations: {'yes' if family.grounding else 'no'}",
    )
    lines.append(f"  Viewing: {family.preview_render}")
    lines.append(f"  Macros: {family.macros}")
    lines.append(f"  Encryption: {family.encryption}")
    for limit in family.known_limits:
        lines.append(f"  Known limit: {limit}")
    return lines


def _summary_lines(matrix: FormatMatrix) -> list[str]:
    """One line per family, ordered as committed."""

    lines: list[str] = []
    for family in matrix.families:
        suffixes = " ".join(family.suffixes)
        extraction = _EXTRACTION_TEXT.get(family.text_extraction, family.text_extraction)
        label = "PARTIAL" if family.support_level == "partial" else "FULL"
        lines.append(f"{family.family:<28} {suffixes:<26} {label:<7} {extraction}")
    return lines


def register_format_matrix_command(app: typer.Typer) -> Callable[..., None]:
    """Attach the read-only tested-format reporting command."""

    @app.command("formats")
    def formats_command(
        suffix: Annotated[
            str | None,
            typer.Argument(
                help="Optional file type to explain, for example pdf, .odt, or notes.docx.",
            ),
        ] = None,
        json_output: Annotated[
            bool,
            typer.Option("--json", help="Emit the machine-readable matrix instead of text."),
        ] = False,
    ) -> None:
        """Show which file formats Archiv accepts and what it verifies for each."""

        try:
            matrix = load_format_matrix(matrix_path())
        except ValueError as error:
            typer.echo(f"formats failed: {error}", err=True)
            raise typer.Exit(code=1) from error

        if suffix is not None:
            requested = _normalize_suffix(suffix)
            try:
                family = matrix.family_for_suffix(requested)
            except KeyError as error:
                rejected = next(
                    (item for item in matrix.rejected_examples if item.suffix == requested),
                    None,
                )
                reason = (
                    rejected.reason
                    if rejected is not None
                    else "not a supported suffix; rejected before any parsing"
                )
                if json_output:
                    typer.echo(
                        json.dumps(
                            {"suffix": requested, "supported": False, "reason": reason},
                            indent=2,
                            sort_keys=True,
                        )
                    )
                else:
                    typer.echo(f"{requested} is not supported.")
                    typer.echo(f"Reason: {reason.rstrip('.')}.")
                    typer.echo("Run 'archiv formats' to list every supported type.")
                raise typer.Exit(code=1) from error

            if json_output:
                typer.echo(
                    json.dumps(family.model_dump(mode="json"), indent=2, sort_keys=True),
                )
                return
            for line in _family_lines(family):
                typer.echo(line)
            return

        if json_output:
            typer.echo(json.dumps(matrix.model_dump(mode="json"), indent=2, sort_keys=True))
            return

        supported = sorted({item for family in matrix.families for item in family.suffixes})
        typer.echo(f"Archiv {matrix.product_version} accepts {len(supported)} file types:")
        typer.echo("")
        for line in _summary_lines(matrix):
            typer.echo(line)
        typer.echo("")
        typer.echo(f"Suffixes: {' '.join(supported)}")
        if matrix.rejected_examples:
            rejected = " ".join(item.suffix for item in matrix.rejected_examples)
            typer.echo(f"Explicitly rejected: {rejected}")
        typer.echo("")
        typer.echo("Every claim above is re-verified against live ingestion runs by the")
        typer.echo("acceptance suite. Run 'archiv formats <type>' for per-format detail.")

    return formats_command
