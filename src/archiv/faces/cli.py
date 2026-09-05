"""Command-line interface for face clustering, opt-in biometrics, and identity resolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from archiv.faces.attributions import (
    attribute_all_clusters,
    attribute_cluster,
    find_cluster_by_target,
)
from archiv.faces.clustering import scan_and_cluster_faces
from archiv.faces.config import (
    BiometricsDisabledError,
    check_faces_opt_in,
    load_face_config,
    save_face_config,
)
from archiv.faces.storage import (
    confirm_cluster_name,
    connect_face_index,
    face_index_path,
    forget_face_data,
    revoke_cluster_confirmation,
)
from archiv.storage.layout import ArchivLayout

faces_app = typer.Typer(
    no_args_is_help=True,
    help="Biometric opt-in, face scanning, clustering, and erasure.",
)
console = Console()


@faces_app.command("opt-in")
def opt_in_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
) -> None:
    """Enable biometric face analysis (GDPR Art. 9 / BIPA compliant)."""
    cfg = load_face_config(home)
    cfg.opt_in = True
    cfg.opt_in_at = datetime.now(UTC).isoformat()
    save_face_config(cfg, home)
    console.print("[bold green]Face analysis enabled.[/bold green]")
    console.print(
        "[dim]Notice: All biometric vectors and face detections are strictly locally derived, "
        "never transmitted off this machine, and can be permanently erased at any time "
        "with 'archiv faces forget'.[/dim]"
    )


@faces_app.command("opt-out")
def opt_out_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
) -> None:
    """Disable biometric face analysis."""
    cfg = load_face_config(home)
    cfg.opt_in = False
    save_face_config(cfg, home)
    console.print("[yellow]Face analysis disabled.[/yellow]")


@faces_app.command("status")
def status_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Show face analysis configuration and database statistics."""
    layout = ArchivLayout.resolve(home)
    cfg = load_face_config(home)
    db_path = face_index_path(layout)

    faces_count = 0
    clusters_count = 0
    confirmations_count = 0

    if db_path.is_file():
        try:
            with connect_face_index(db_path) as conn:
                r1 = conn.execute("SELECT COUNT(*) as c FROM faces").fetchone()
                faces_count = int(r1["c"]) if r1 else 0
                r2 = conn.execute("SELECT COUNT(*) as c FROM face_clusters").fetchone()
                clusters_count = int(r2["c"]) if r2 else 0
                r3 = conn.execute("SELECT COUNT(*) as c FROM confirmations").fetchone()
                confirmations_count = int(r3["c"]) if r3 else 0
        except Exception:
            pass

    payload = {
        "opt_in": cfg.opt_in,
        "opt_in_at": cfg.opt_in_at,
        "similarity_threshold": cfg.similarity_threshold,
        "min_detection_confidence": cfg.min_detection_confidence,
        "database_path": str(db_path),
        "database_exists": db_path.is_file(),
        "total_faces": faces_count,
        "total_clusters": clusters_count,
        "total_confirmations": confirmations_count,
    }

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    table = Table(title="Archiv Face Analysis Status")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row(
        "Opt-in Status",
        "[green]ENABLED[/green]" if cfg.opt_in else "[yellow]DISABLED (Default)[/yellow]",
    )
    if cfg.opt_in_at:
        table.add_row("Opted In At", cfg.opt_in_at)
    table.add_row("Similarity Threshold", str(cfg.similarity_threshold))
    table.add_row("Min Detection Confidence", str(cfg.min_detection_confidence))
    table.add_row("Database Exists", "Yes" if db_path.is_file() else "No")
    table.add_row("Total Faces Detected", str(faces_count))
    table.add_row("Total Clusters", str(clusters_count))
    table.add_row("Confirmed Identities", str(confirmations_count))
    console.print(table)


@faces_app.command("scan")
def scan_command(
    threshold: Annotated[
        float | None,
        typer.Option("--threshold", "-t", min=0.0, max=1.0, help="Clustering cosine threshold."),
    ] = None,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Scan ingested image objects for faces and update person clusters."""
    try:
        check_faces_opt_in(home)
    except BiometricsDisabledError as err:
        console.print(f"[bold red]Opt-in Required:[/bold red] {err}")
        raise typer.Exit(code=1) from err

    faces_found, clusters_total = scan_and_cluster_faces(home=home, threshold=threshold)
    if json_output:
        typer.echo(
            json.dumps(
                {"faces_detected": faces_found, "total_clusters": clusters_total},
                indent=2,
            )
        )
    else:
        console.print(
            f"[bold green]Face scan complete:[/bold green] {faces_found} new face(s) detected, "
            f"{clusters_total} total individual cluster(s)."
        )


@faces_app.command("list")
def list_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """List all detected face clusters and their attribution status."""
    try:
        check_faces_opt_in(home)
    except BiometricsDisabledError as err:
        console.print(f"[bold red]Opt-in Required:[/bold red] {err}")
        raise typer.Exit(code=1) from err

    attributions = attribute_all_clusters(home=home)
    if json_output:
        typer.echo(json.dumps([a.model_dump(mode="json") for a in attributions], indent=2))
        return

    if not attributions:
        console.print("[yellow]No face clusters found. Run 'archiv faces scan' first.[/yellow]")
        return

    table = Table(title="Detected Face Clusters")
    table.add_column("Cluster ID", style="dim")
    table.add_column("Label", style="bold")
    table.add_column("Members", justify="right")
    table.add_column("Status")
    table.add_column("Identity / Candidate Hypothesis")

    for attr in attributions:
        if attr.status == "confirmed":
            status_str = "[green]Confirmed[/green]"
            id_str = f"[bold green]{attr.confirmed_name}[/bold green]"
        else:
            status_str = "[yellow]Unconfirmed[/yellow]"
            if attr.candidates:
                top = attr.candidates[0]
                id_str = f"{top.name} [dim]({top.confidence * 100:.0f}% conf)[/dim]"
            else:
                id_str = "[dim]No candidates[/dim]"

        table.add_row(
            attr.cluster_id,
            attr.label,
            str(attr.member_count),
            status_str,
            id_str,
        )

    console.print(table)


@faces_app.command("forget")
def forget_command(
    cluster_id: Annotated[
        str | None,
        typer.Argument(help="Cluster ID to permanently erase. If omitted with --all, erases all."),
    ] = None,
    all_data: Annotated[
        bool,
        typer.Option("--all", help="Erase all biometric vectors, detections, and clusters."),
    ] = False,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
) -> None:
    """First-class erasure: permanently purge biometric vectors and detections."""
    layout = ArchivLayout.resolve(home)
    if not all_data and cluster_id is None:
        console.print(
            "[red]Specify a cluster ID to forget or use '--all' to erase all face data.[/red]"
        )
        raise typer.Exit(code=1)

    target_cid = None if all_data else cluster_id
    count = forget_face_data(layout, target_cid)
    if target_cid:
        console.print(
            f"[bold green]Biometric erasure complete:[/bold green] Erased cluster '{target_cid}' "
            f"({count} face detections purged). Original image files remain untouched."
        )
    else:
        console.print(
            f"[bold green]Biometric erasure complete:[/bold green] Erased all face biometric data "
            f"({count} detections purged). Original image files remain untouched."
        )


def who_command(
    target: Annotated[
        str,
        typer.Argument(
            help="Cluster ID, label ('Person 1'), or confirmed name to inspect or attribute."
        ),
    ],
    confirm: Annotated[
        str | None,
        typer.Option("--confirm", "-c", help="Confirm a name for this individual cluster."),
    ] = None,
    revoke: Annotated[
        bool,
        typer.Option("--revoke", help="Revoke a previously confirmed name for this cluster."),
    ] = False,
    explain: Annotated[
        bool,
        typer.Option("--explain", "-e", help="Show granular citation provenance and detections."),
    ] = False,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Attribution and identity lifecycle for face clusters (never auto-asserts)."""
    try:
        check_faces_opt_in(home)
    except BiometricsDisabledError as err:
        console.print(f"[bold red]Opt-in Required:[/bold red] {err}")
        raise typer.Exit(code=1) from err

    layout = ArchivLayout.resolve(home)
    found = find_cluster_by_target(layout, target)
    if not found:
        console.print(f"[red]No cluster or person found matching:[/red] '{target}'")
        raise typer.Exit(code=1)

    cluster_id, label = found

    if confirm:
        confirm_cluster_name(layout, cluster_id, confirm)
        console.print(
            f"[bold green]Identity confirmed:[/bold green] Cluster '{label}' ({cluster_id}) "
            f"is now confirmed as '{confirm.strip()}'."
        )

    if revoke:
        revoked = revoke_cluster_confirmation(layout, cluster_id)
        if revoked:
            console.print(
                f"[yellow]Confirmation revoked:[/yellow] Cluster '{label}' ({cluster_id}) "
                f"reverted to unconfirmed."
            )
        else:
            console.print(f"[yellow]Cluster '{label}' was not confirmed.[/yellow]")

    attr = attribute_cluster(layout, cluster_id, label)
    if attr is None:
        console.print(f"[red]Could not retrieve attribution for cluster {cluster_id}.[/red]")
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(attr.model_dump_json(indent=2))
        return

    # Render attribution display
    console.print(f"[bold cyan]Cluster Attribution:[/bold cyan] {attr.label} ({attr.cluster_id})")
    console.print(f"Members: {attr.member_count} image(s)")
    if attr.status == "confirmed":
        console.print(
            f"Status: [bold green]Confirmed[/bold green] as '[bold]{attr.confirmed_name}[/bold]' "
            f"[dim](at {attr.confirmed_at})[/dim]"
        )
    else:
        console.print("Status: [yellow]Unconfirmed[/yellow] (identity never auto-asserted)")

    if explain or attr.status != "confirmed":
        if attr.candidates:
            table = Table(title="Candidate Name Hypotheses & Citations")
            table.add_column("Rank", justify="right")
            table.add_column("Candidate Name", style="bold")
            table.add_column("Confidence", justify="right")
            table.add_column("Supporting Citations")

            for idx, cand in enumerate(attr.candidates, 1):
                cit_lines = [
                    f"[{c.source_type.upper()}] {c.detail} ({c.source_name})"
                    for c in cand.supporting_citations
                ]
                table.add_row(
                    str(idx),
                    cand.name,
                    f"{cand.confidence * 100:.1f}%",
                    "\n".join(cit_lines) if cit_lines else "[dim]None[/dim]",
                )
            console.print(table)
        else:
            console.print("[dim]No candidate name citations found for this cluster.[/dim]")

    if explain:
        # Show member detections
        db_path = face_index_path(layout)
        with connect_face_index(db_path) as conn:
            members = conn.execute(
                """
                SELECT face_id, object_sha256, source_name, bbox_json, confidence
                FROM faces
                WHERE cluster_id = ?
                """,
                (cluster_id,),
            ).fetchall()

        table_m = Table(title="Cluster Member Face Detections")
        table_m.add_column("Face ID", style="dim")
        table_m.add_column("Source Image", style="bold")
        table_m.add_column("Object SHA-256", style="dim")
        table_m.add_column("Bounding Box [x0, y0, x1, y1]")
        table_m.add_column("Confidence", justify="right")

        for m in members:
            table_m.add_row(
                str(m["face_id"]),
                str(m["source_name"]),
                str(m["object_sha256"])[:12],
                str(m["bbox_json"]),
                f"{float(m['confidence']) * 100:.1f}%",
            )
        console.print(table_m)


def register_faces_commands(app: typer.Typer) -> None:
    """Register 'faces' and 'who' commands on the root app."""
    app.add_typer(faces_app, name="faces")
    app.command("who")(who_command)
