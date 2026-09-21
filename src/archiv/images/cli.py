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
    count_image_objects,
    image_index_path,
    rebuild_image_index,
)
from archiv.images.search import MATCH_FLOOR, NotAnImageError, find_similar_images
from archiv.storage.layout import ArchivLayout

images_app = typer.Typer(
    no_args_is_help=True,
    help="Find images that look like a given image, and manage the image index.",
)
console = Console()

DUPLICATE_DETECTION_REFUSAL = (
    "Image duplicate detection is temporarily unavailable. The previous method could not "
    "safely distinguish some unrelated images, so Archiv will not report duplicate groups "
    "from it while the replacement is being built. No duplicate groups were reported. "
    "Exact-content duplicate tracking during ingestion is unchanged."
)


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


@images_app.command("find-similar")
def find_similar_command(
    image: Annotated[
        Path,
        typer.Option(
            "--image",
            dir_okay=False,
            help="Path to an image to find near-duplicates of.",
        ),
    ],
    top_k: Annotated[int, typer.Option("--top-k", "-k", min=1, help="Max results to return.")] = 10,
    min_score: Annotated[
        float,
        typer.Option(
            "--min-score",
            min=-1.0,
            max=1.0,
            help=f"Similarity floor. Default {MATCH_FLOOR}, which is a measured figure.",
        ),
    ] = MATCH_FLOOR,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Find indexed images that look like the given image."""

    try:
        results = find_similar_images(image, top_k=top_k, min_score=min_score, home=home)
    except NotAnImageError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    if json_output:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return

    if not results:
        if not image_index_path(ArchivLayout.resolve(home)).is_file():
            console.print(
                "[yellow]This archive has no image index yet, so nothing can be "
                "compared.[/yellow] Build one with [bold]archiv images "
                "rebuild-index[/bold]."
            )
            return
        console.print(
            f"[yellow]Nothing in this archive looks like[/yellow] {image.name} "
            f"[yellow]at a similarity of {min_score} or above.[/yellow]"
        )
        return

    table = Table(title=f"Images similar to {image.name}")
    table.add_column("Rank", justify="right", style="cyan")
    table.add_column("Similarity", justify="right", style="green")
    table.add_column("Source Name", style="bold")
    table.add_column("Dimensions")
    table.add_column("SHA-256 (prefix)", style="dim")

    for rank, res in enumerate(results, 1):
        table.add_row(
            str(rank),
            f"{res.score:.2f}",
            res.source_name,
            f"{res.width}x{res.height}",
            res.object_sha256[:12],
        )

    console.print(table)
    console.print(
        "[dim]Similarity is a raw cosine over colour and edge features, not a calibrated "
        "confidence. What decides whether it can be trusted is how close two images' "
        "overall colour balance is: the closer it is, the less this can tell a duplicate "
        "from an unrelated picture, and on light pages of dark text it cannot tell them "
        "apart at all. Two unrelated photographs sharing a colour character can score "
        "above 0.99. See docs/known-issues.md.[/dim]"
    )


@images_app.command("duplicates")
def duplicates_command(
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", min=0.0, max=1.0, help="Cosine similarity threshold."),
    ] = 0.95,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Refuse duplicate grouping until an evidence-backed replacement is available."""
    _ = (threshold, home)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "unsafe_similarity_method",
                    "message": DUPLICATE_DETECTION_REFUSAL,
                    "duplicate_groups": [],
                },
                indent=2,
            )
        )
    else:
        typer.echo(DUPLICATE_DETECTION_REFUSAL, err=True)
    raise typer.Exit(code=2)


@images_app.command("status")
def status_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show the status of the image embedding index against canonical image objects."""
    layout = ArchivLayout.resolve(home)
    index_file = image_index_path(layout)
    total = count_image_objects(layout)

    if not index_file.is_file():
        status = {
            "status": "missing",
            "path": str(index_file),
            "images_indexed": 0,
            "images_total": total,
        }
    else:
        try:
            with connect_image_index(index_file) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), model_name, dimensions "
                    "FROM image_embeddings GROUP BY model_name, dimensions"
                ).fetchone()
                count = int(row[0]) if row else 0
                model_name = str(row[1]) if row else "none"
                dimensions = int(row[2]) if row else 0
            status = {
                "status": "ready" if count == total else "stale",
                "path": str(index_file),
                "images_indexed": count,
                "images_total": total,
                "model_name": model_name,
                "dimensions": dimensions,
                "size_bytes": index_file.stat().st_size,
            }
        except Exception as error:
            status = {
                "status": "error",
                "path": str(index_file),
                "images_total": total,
                "error": str(error),
            }

    if json_output:
        typer.echo(json.dumps(status, indent=2))
    else:
        if status["status"] in {"ready", "stale"}:
            size_kb = float(status.get("size_bytes", 0)) / 1024.0
            label = "ready" if status["status"] == "ready" else "stale"
            console.print(
                f"Image index {label}: {status['images_indexed']} of "
                f"{status['images_total']} images indexed "
                f"(model: {status['model_name']}, {status['dimensions']} dims, {size_kb:.1f} KB)"
            )
        elif status["status"] == "missing":
            console.print(
                f"[yellow]Image index missing:[/yellow] 0 of {status['images_total']} images indexed. "
                f"Run 'archiv images rebuild-index' to create {status['path']}."
            )
        else:
            console.print(f"[red]Image index error:[/red] {status.get('error')}")


def register_image_commands(app: typer.Typer) -> None:
    """Register image search and duplicate commands on the root app."""
    app.add_typer(images_app, name="images")
