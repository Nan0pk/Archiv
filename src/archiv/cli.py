"""Archiv command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from archiv import __version__
from archiv.alpha_cli import register_alpha_commands
from archiv.contracts import RunStatus
from archiv.doctor import diagnostics_report, doctor_report, save_diagnostics
from archiv.executor.source_marker import run_source_marker
from archiv.format_matrix_cli import register_format_matrix_command
from archiv.ingestion import ingest_file, rebuild_derived
from archiv.ingestion.formats import UnsupportedFormatError, suffix_for
from archiv.model_cli import model_app
from archiv.ocr_benchmark_cli import register_ocr_benchmark_command
from archiv.report_cli import register_report_commands
from archiv.search import rebuild_search_index, search_documents
from archiv.source_location_cli import register_source_location_command
from archiv.ui_cli import register_ui_command
from archiv.user_cli import register_user_commands

app = typer.Typer(no_args_is_help=True, help="Archiv local-first knowledge-work core.")
console = Console()
app.add_typer(model_app, name="model")
register_report_commands(app)
register_alpha_commands(app)
register_user_commands(app)
register_source_location_command(app)
register_format_matrix_command(app)
register_ocr_benchmark_command(app)
register_ui_command(app)


@app.command()
def version() -> None:
    """Print the installed Archiv version."""

    typer.echo(__version__)


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
) -> None:
    """Check the deterministic minimum development environment."""

    report = doctor_report(home)
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


@app.command("diagnostics-export")
def diagnostics_export(
    destination: Annotated[Path, typer.Argument(help="New JSON support-bundle file.")],
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Save after printing the full preview.")
    ] = False,
) -> None:
    """Preview every diagnostics field, then optionally save the support bundle."""

    report = diagnostics_report(home)
    typer.echo("Diagnostics preview (this is exactly what will be saved):")
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    if not yes and not typer.confirm("Save this diagnostics bundle?"):
        typer.echo("Not saved.")
        return
    try:
        save_diagnostics(report, destination)
    except OSError as error:
        typer.echo(f"diagnostics export failed: {type(error).__name__}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo("Saved diagnostics bundle.")


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
            dir_okay=True,
            readable=True,
            resolve_path=True,
            help="Local file or directory to preserve and normalize.",
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
        if source.is_file():
            result = ingest_file(source, home=home, rebuild_derived=rebuild)
            typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
            return

        def _is_supported_candidate(p: Path) -> bool:
            try:
                suffix_for(p.name)
                return True
            except UnsupportedFormatError:
                return False

        candidates = sorted(
            path for path in source.rglob("*") if path.is_file() and _is_supported_candidate(path)
        )
        if not candidates:
            raise ValueError("directory contains no supported files")
        results = [
            ingest_file(path, home=home, rebuild_derived=rebuild).model_dump(mode="json")
            for path in candidates
        ]
        index = rebuild_search_index(home=home).model_dump(mode="json")
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"ingestion failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "schema_version": "1",
                "status": "succeeded",
                "source_directory": str(source),
                "ingested": results,
                "search_index": index,
            },
            indent=2,
            sort_keys=True,
        )
    )


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


@app.command("rebuild-search-index")
def rebuild_search_index_command(
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
    """Atomically rebuild the replaceable SQLite FTS5 index."""

    try:
        result = rebuild_search_index(home=home)
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"index rebuild failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("search")
def search_command(
    query: Annotated[str, typer.Argument(help="Literal text or phrase to find.")],
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
    source_name: Annotated[str | None, typer.Option("--source-name")] = None,
    media_type: Annotated[str | None, typer.Option("--media-type")] = None,
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    object_sha256: Annotated[str | None, typer.Option("--object-sha256")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
) -> None:
    """Search normalized text and emit validated exact citations."""

    try:
        results = search_documents(
            query,
            home=home,
            source_name=source_name,
            media_type=media_type,
            kind=kind,
            object_sha256=object_sha256,
            limit=limit,
        )
    except (OSError, RuntimeError, ValueError) as error:
        typer.echo(f"search failed: {type(error).__name__}: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            [result.model_dump(mode="json") for result in results],
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
