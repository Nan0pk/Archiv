"""Archiv command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from archiv import __version__
from archiv.contracts import RunStatus
from archiv.doctor import doctor_report
from archiv.executor.source_marker import run_source_marker
from archiv.ingestion import ingest_file, rebuild_derived

app = typer.Typer(no_args_is_help=True, help="Archiv local-first knowledge-work core.")
console = Console()


@app.command()
def version() -> None:
    """Print the installed Archiv version."""

    typer.echo(__version__)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Check the deterministic minimum development environment."""

    report = doctor_report()
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        table = Table(title="Archiv doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        for check in report["checks"]:
            table.add_row(
                check["name"],
                "PASS" if check["passed"] else "FAIL",
                check["detail"],
            )
        console.print(table)

    if report["status"] != "ok":
        raise typer.Exit(code=1)


@app.command("source-marker")
def source_marker_command(
    workspace: Annotated[
        Path,
        typer.Option(
            "--workspace",
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Clean workspace containing source.txt.",
        ),
    ] = Path("."),
    json_output: Annotated[bool, typer.Option("--json/--no-json")] = True,
) -> None:
    """Run and independently validate the exact source-marker task."""

    result = run_source_marker(workspace)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        typer.echo(f"{result.status}: {result.evidence_dir}")

    if result.status is not RunStatus.SUCCEEDED:
        raise typer.Exit(code=1)


@app.command("ingest")
def ingest_command(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local file to preserve and normalize.",
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
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild-derived", help="Replace derived outputs after ingestion."),
    ] = False,
) -> None:
    """Validate and ingest one file into immutable local storage."""

    try:
        result = ingest_file(source, home=home, rebuild_derived=rebuild)
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"ingestion failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("rebuild-derived")
def rebuild_derived_command(
    digest: Annotated[str, typer.Argument(help="Lowercase SHA-256 object digest.")],
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
) -> None:
    """Delete only one object's derived data and rebuild it from the original."""

    try:
        evidence = rebuild_derived(digest, home=home)
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"rebuild failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            [item.model_dump(mode="json") for item in evidence],
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
