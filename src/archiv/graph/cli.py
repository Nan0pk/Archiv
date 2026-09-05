"""Command-line interface for the evidence-backed entity graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from archiv.graph.builder import rebuild_graph
from archiv.graph.queries import get_entity_profile, query_cross_corpus
from archiv.graph.storage import connect_graph_index, get_graph_stats, graph_index_path
from archiv.storage.layout import ArchivLayout

graph_app = typer.Typer(
    no_args_is_help=True,
    help="Entity graph, cross-corpus traversals, and evidence-backed relationship queries.",
)
console = Console()


@graph_app.command("rebuild")
def rebuild_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Rebuild the SQLite entity graph from canonical objects, derived segments, and faces."""
    nodes_count, edges_count = rebuild_graph(home=home)
    if json_output:
        typer.echo(json.dumps({"nodes_count": nodes_count, "edges_count": edges_count}, indent=2))
    else:
        console.print(
            f"[bold green]Entity graph rebuilt:[/bold green] {nodes_count} nodes, "
            f"{edges_count} evidence-backed edges."
        )


@graph_app.command("stats")
def stats_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show summary statistics of entities, relationships, and evidence edges."""
    layout = ArchivLayout.resolve(home)
    db_path = graph_index_path(layout)
    if not db_path.is_file():
        console.print(
            "[yellow]Entity graph has not been built yet. Run 'archiv graph rebuild'.[/yellow]"
        )
        return

    with connect_graph_index(db_path) as conn:
        stats = get_graph_stats(conn)

    if json_output:
        typer.echo(json.dumps(stats, indent=2))
        return

    table = Table(title="Entity Graph Statistics")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("Total Entities (Nodes)", str(stats["total_nodes"]))
    table.add_row("Total Relationships (Edges)", str(stats["total_edges"]))

    for ntype, count in stats.get("nodes_by_type", {}).items():
        table.add_row(f"  • Node: {ntype}", str(count))

    for rel, count in stats.get("edges_by_relation", {}).items():
        table.add_row(f"  • Edge: {rel}", str(count))

    for status, count in stats.get("edges_by_status", {}).items():
        color = "green" if status == "confirmed" else ("cyan" if status == "probable" else "yellow")
        table.add_row(f"  • Status: [{color}]{status}[/{color}]", str(count))

    console.print(table)


@graph_app.command("query")
def query_command(
    person: Annotated[
        str | None,
        typer.Option("--person", "-p", help="Filter by person name."),
    ] = None,
    date_from: Annotated[
        int | None,
        typer.Option("--date-from", help="Start year (inclusive)."),
    ] = None,
    date_to: Annotated[
        int | None,
        typer.Option("--date-to", help="End year (inclusive)."),
    ] = None,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Query across photographs and mentioning documents."""
    results = query_cross_corpus(
        person_name=person,
        date_from=date_from,
        date_to=date_to,
        home=home,
    )

    if json_output:
        typer.echo(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
        return

    if not results:
        console.print("[yellow]No matching entities found for query criteria.[/yellow]")
        return

    table = Table(title="Cross-Corpus Traversal Results")
    table.add_column("Person", style="bold")
    table.add_column("Status")
    table.add_column("Photographs Appeared In")
    table.add_column("Documents Mentioning Person")

    for res in results:
        status_color = "green" if res.status == "confirmed" else "yellow"
        photo_lines = [
            f"📷 {p.image_name} ({p.year or 'undated'}) [dim]{p.confidence * 100:.0f}% conf[/dim]"
            for p in res.photographs
        ]
        doc_lines = [
            f"📄 {d.document_name} [dim]'{d.snippet[:50]}...'[/dim]"
            for d in res.mentioning_documents
        ]

        table.add_row(
            res.person_name,
            f"[{status_color}]{res.status}[/{status_color}]",
            "\n".join(photo_lines) if photo_lines else "[dim]None[/dim]",
            "\n".join(doc_lines) if doc_lines else "[dim]None[/dim]",
        )

    console.print(table)


@graph_app.command("entity")
def entity_command(
    target: Annotated[str, typer.Argument(help="Entity name or node ID to inspect.")],
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Inspect complete 360-degree graph profile and evidence citations of an entity."""
    profile = get_entity_profile(target, home=home)
    if not profile:
        console.print(f"[red]Entity not found:[/red] '{target}'")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(profile.model_dump_json(indent=2))
        return

    console.print(
        f"[bold cyan]Entity Profile:[/bold cyan] {profile.entity.canonical_name} "
        f"({profile.entity.entity_type})"
    )

    if profile.appearances:
        t_app = Table(title="Photograph Appearances")
        t_app.add_column("Image", style="bold")
        t_app.add_column("Year")
        t_app.add_column("Confidence")
        t_app.add_column("Status")
        t_app.add_column("Citation Detail")
        for app in profile.appearances:
            t_app.add_row(
                app.image_name,
                str(app.year or "—"),
                f"{app.confidence * 100:.1f}%",
                app.status,
                app.citations[0].snippet if app.citations else "",
            )
        console.print(t_app)

    if profile.mentions:
        t_men = Table(title="Document Mentions")
        t_men.add_column("Document", style="bold")
        t_men.add_column("Locator", style="dim")
        t_men.add_column("Snippet")
        for men in profile.mentions:
            t_men.add_row(
                men.document_name,
                str(men.locator),
                men.snippet,
            )
        console.print(t_men)

    if profile.co_occurrences:
        t_co = Table(title="Co-occurring Entities")
        t_co.add_column("Entity", style="bold")
        t_co.add_column("Type")
        t_co.add_column("Confidence")
        for co in profile.co_occurrences:
            t_co.add_row(
                str(co["entity_name"]),
                str(co["entity_type"]),
                f"{float(co['confidence']) * 100:.1f}%",
            )
        console.print(t_co)


def register_graph_commands(app: typer.Typer) -> None:
    """Register graph commands on root CLI app."""
    app.add_typer(graph_app, name="graph")
