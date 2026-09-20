"""Command-line interface for the evidence-backed entity graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, cast

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


def _without_unmeasured_confidence(value: Any) -> Any:
    """Remove internal graph confidence numbers from user-visible JSON."""
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {
            key: _without_unmeasured_confidence(item)
            for key, item in mapping.items()
            if key != "confidence"
        }
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_without_unmeasured_confidence(item) for item in items]
    return value


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
    """Show graph statistics."""
    layout = ArchivLayout.resolve(home)
    stats = get_graph_stats(layout)
    if json_output:
        typer.echo(json.dumps(stats, indent=2))
    else:
        table = Table(title="Entity graph statistics")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        for key, value in stats.items():
            table.add_row(str(key), str(value))
        console.print(table)


@graph_app.command("entity")
def entity_command(
    name: Annotated[str, typer.Argument(help="Entity name to inspect.")],
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show an entity profile and its evidence-backed relationships."""
    layout = ArchivLayout.resolve(home)
    path = graph_index_path(layout)
    if not path.is_file():
        raise typer.BadParameter("Entity graph index not found. Run `archiv graph rebuild` first.")
    with connect_graph_index(path, read_only=True) as conn:
        profile = get_entity_profile(conn, name)
    if profile is None:
        raise typer.BadParameter(f"Entity not found: {name}")
    if json_output:
        typer.echo(json.dumps(_without_unmeasured_confidence(profile), indent=2))
        return

    node = profile["node"]
    console.print(f"[bold]Entity Profile:[/bold] {node['canonical_name']} ({node['entity_type']})")
    relationships = profile["relationships"]
    if not relationships:
        console.print("No relationships found.")
        return
    table = Table(title="Relationships")
    table.add_column("Relation")
    table.add_column("Entity")
    table.add_column("Status")
    table.add_column("Evidence")
    for relationship in relationships:
        citations = relationship.get("citations", [])
        evidence = ", ".join(str(citation.get("source_name", "unknown")) for citation in citations)
        table.add_row(
            str(relationship.get("relation_type", "")),
            str(relationship.get("other_name", "")),
            str(relationship.get("status", "")),
            evidence,
        )
    console.print(table)


@graph_app.command("query")
def query_command(
    query: Annotated[str, typer.Argument(help="Cross-corpus graph query.")],
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Run a cross-corpus graph query."""
    layout = ArchivLayout.resolve(home)
    path = graph_index_path(layout)
    if not path.is_file():
        raise typer.BadParameter("Entity graph index not found. Run `archiv graph rebuild` first.")
    with connect_graph_index(path, read_only=True) as conn:
        results = query_cross_corpus(conn, query)
    if json_output:
        typer.echo(json.dumps(_without_unmeasured_confidence(results), indent=2))
        return
    if not results:
        console.print("No graph relationships matched the query.")
        return
    table = Table(title="Graph query results")
    table.add_column("Source")
    table.add_column("Relation")
    table.add_column("Target")
    table.add_column("Status")
    for result in results:
        table.add_row(
            str(result.get("source_name", "")),
            str(result.get("relation_type", "")),
            str(result.get("target_name", "")),
            str(result.get("status", "")),
        )
    console.print(table)
