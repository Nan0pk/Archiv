"""Archiv command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from archiv import __version__
from archiv.contracts import RunStatus
from archiv.doctor import doctor_report
from archiv.executor.source_marker import run_source_marker

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
    workspace: Path = typer.Option(
        Path("."),
        "--workspace",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Clean workspace containing source.txt.",
    ),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Run and independently validate the exact source-marker task."""

    result = run_source_marker(workspace)
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        typer.echo(f"{result.status}: {result.evidence_dir}")

    if result.status is not RunStatus.SUCCEEDED:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
