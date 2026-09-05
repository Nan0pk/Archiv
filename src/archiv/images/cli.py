"""Command-line interface for image search and near-duplicate detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from archiv.images.index import (
    connect_image_index,
    image_index_path,
    rebuild_image_index,
)
from archiv.images.search import find_near_duplicates, search_images
from archiv.storage.layout import ArchivLayout

images_app = typer.Typer(
    no_args_is_help=True,
    help="Semantic image search, embeddings index, and near-duplicate detection.",
)
console = Console()


@images_app.command("rebuild-index")
def rebuild_index_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Rebuild the SQLite image embedding index from canonical objects."""
    result = rebuild_image_index(home=home)
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        console.print(
            f"[bold green]Rebuilt image index:[/bold green] "
            f"{result.object_count} images indexed in {result.elapsed_seconds:.2f}s "
            f"({result.index_size_bytes / 1024:.1f} KB, model: {result.model_name})"
        )


@images_app.command("search")
def search_command(
    query: Annotated[str, typer.Argument(help="Semantic description or image path to search for.")],
    top_k: Annotated[int, typer.Option("--top-k", "-k", min=1, help="Max results to return.")] = 10,
    min_score: Annotated[
        float, typer.Option("--min-score", min=-1.0, max=1.0, help="Minimum cosine similarity.")
    ] = 0.0,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Search images by text description or reference image similarity."""
    results = search_images(query, top_k=top_k, min_score=min_score, home=home)
    if json_output:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return

    if not results:
        console.print(f"[yellow]No matching images found for:[/yellow] {query}")
        return

    table = Table(title=f"Image Search: {query}")
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Source Name", style="bold")
    table.add_column("Dimensions")
    table.add_column("SHA-256 (prefix)", style="dim")

    for rank, res in enumerate(results, 1):
        table.add_row(
            str(rank),
            f"{res.score:.4f}",
            res.source_name,
            f"{res.width}x{res.height}",
            res.object_sha256[:12],
        )

    console.print(table)


@images_app.command("duplicates")
def duplicates_command(
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", min=0.0, max=1.0, help="Cosine similarity threshold."),
    ] = 0.95,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Find near-duplicate image clusters across the corpus."""
    groups = find_near_duplicates(threshold=threshold, home=home)
    if json_output:
        typer.echo(json.dumps([g.model_dump(mode="json") for g in groups], indent=2))
        return

    if not groups:
        console.print(f"[green]No duplicate images found above threshold {threshold:.2f}.[/green]")
        return

    console.print(
        f"[bold yellow]Found {len(groups)} duplicate cluster(s) "
        f"(threshold >= {threshold:.2f}):[/bold yellow]"
    )
    for idx, group in enumerate(groups, 1):
        lead_prefix = group.lead_sha256[:12]
        console.print(
            f"\n[bold]Cluster #{idx}:[/bold] "
            f"Lead: [cyan]{group.lead_source_name}[/cyan] ({lead_prefix})"
        )
        for member in group.members:
            m_prefix = member.object_sha256[:12]
            sim_str = f"{member.similarity_to_lead:.4f}"
            console.print(
                f"  - [green]{member.source_name}[/green] ({m_prefix}) similarity: {sim_str}"
            )


@images_app.command("status")
def status_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show the status of the image embedding index."""
    layout = ArchivLayout.resolve(home)
    index_file = image_index_path(layout)

    if not index_file.is_file():
        status = {"status": "missing", "path": str(index_file), "images_indexed": 0}
    else:
        try:
            with connect_image_index(index_file) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), model_name, dimensions "
                    "FROM image_embeddings GROUP BY model_name, dimensions"
                ).fetchone()
                count = row[0] if row else 0
                model_name = row[1] if row else "none"
                dimensions = row[2] if row else 0
            status = {
                "status": "ready",
                "path": str(index_file),
                "images_indexed": count,
                "model_name": model_name,
                "dimensions": dimensions,
                "size_bytes": index_file.stat().st_size,
            }
        except Exception as error:
            status = {"status": "error", "path": str(index_file), "error": str(error)}

    if json_output:
        typer.echo(json.dumps(status, indent=2))
    else:
        if status["status"] == "ready":
            size_kb = float(status.get("size_bytes", 0)) / 1024.0
            console.print(
                f"[green]Image index ready:[/green] {status['images_indexed']} images indexed "
                f"(model: {status['model_name']}, {status['dimensions']} dims, {size_kb:.1f} KB)"
            )
        elif status["status"] == "missing":
            console.print(
                f"[yellow]Image index missing:[/yellow] {status['path']}. "
                f"Run 'archiv images rebuild-index' to create it."
            )
        else:
            console.print(f"[red]Image index error:[/red] {status.get('error')}")


def register_image_commands(app: typer.Typer) -> None:
    """Register image search and duplicate commands on the root app."""
    app.add_typer(images_app, name="images")
