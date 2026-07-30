"""CLI registration for evidence-backed DOCX report commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from archiv.report_contracts import ReportStatus
from archiv.reports import generate_report, validate_report
from archiv.reports.validation import write_validation


def register_report_commands(app: typer.Typer) -> None:
    """Attach report generation and verification commands to the root CLI."""

    @app.command("generate-report")
    def generate_report_command(
        query: Annotated[
            str,
            typer.Argument(help="Literal search phrase used for report sources."),
        ],
        output: Annotated[
            Path,
            typer.Argument(
                file_okay=True,
                dir_okay=False,
                resolve_path=True,
                help="Destination DOCX path.",
            ),
        ],
        home: Annotated[
            Path | None,
            typer.Option(
                "--home",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Archiv home. Defaults to ARCHIV_HOME or the user data directory.",
            ),
        ] = None,
        title: Annotated[str, typer.Option("--title")] = "Archiv Evidence Report",
        max_sources: Annotated[int, typer.Option("--max-sources", min=1, max=50)] = 8,
        render: Annotated[bool, typer.Option("--render/--no-render")] = True,
        evidence_dir: Annotated[
            Path | None,
            typer.Option(
                "--evidence-dir",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Directory for rendered PDF and page images.",
            ),
        ] = None,
    ) -> None:
        """Generate a cited DOCX and report success only after validation."""

        try:
            result = generate_report(
                query,
                output,
                title=title,
                home=home,
                max_sources=max_sources,
                render=render,
                evidence_dir=evidence_dir,
            )
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"report generation failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        if result.status is not ReportStatus.SUCCEEDED:
            raise typer.Exit(code=1)

    @app.command("verify-report")
    def verify_report_command(
        docx_path: Annotated[
            Path,
            typer.Argument(
                exists=True,
                file_okay=True,
                dir_okay=False,
                readable=True,
                resolve_path=True,
                help="Generated DOCX to verify.",
            ),
        ],
        manifest_path: Annotated[
            Path,
            typer.Argument(
                exists=True,
                file_okay=True,
                dir_okay=False,
                readable=True,
                resolve_path=True,
                help="Report manifest sidecar.",
            ),
        ],
        home: Annotated[
            Path | None,
            typer.Option(
                "--home",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Archiv home. Defaults to ARCHIV_HOME or the user data directory.",
            ),
        ] = None,
        render: Annotated[bool, typer.Option("--render/--no-render")] = True,
        evidence_dir: Annotated[
            Path | None,
            typer.Option(
                "--evidence-dir",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Directory for rendered PDF and page images.",
            ),
        ] = None,
        validation_path: Annotated[
            Path | None,
            typer.Option(
                "--validation-path",
                file_okay=True,
                dir_okay=False,
                resolve_path=True,
                help="Optional JSON file for validation evidence.",
            ),
        ] = None,
    ) -> None:
        """Verify package structure, citations, and optional rendering."""

        try:
            validation = validate_report(
                docx_path,
                manifest_path,
                home=home,
                render=render,
                evidence_dir=evidence_dir,
            )
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"report verification failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        if validation_path is not None:
            validation_path.parent.mkdir(parents=True, exist_ok=True)
            write_validation(validation_path, validation)
        typer.echo(json.dumps(validation.model_dump(mode="json"), indent=2, sort_keys=True))
        if not validation.valid:
            raise typer.Exit(code=1)
