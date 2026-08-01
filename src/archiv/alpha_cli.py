"""CLI registration for offline-alpha lifecycle commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from archiv.archive import ArchiveResult, RestoreResult, create_archive, restore_archive
from archiv.contracts import RunStatus
from archiv.sample_vault import create_sample_vault
from archiv.tasks import run_task, verify_task_run


def _emit(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


def _emit_archive(result: ArchiveResult, *, json_output: bool) -> None:
    if json_output:
        _emit(result)
        return
    label = "Backup" if result.kind == "backup" else "Export"
    typer.echo(f"{label}: {result.archive_path}")
    typer.echo(f"Entries: {result.entry_count}")
    typer.echo(f"SHA-256: {result.archive_sha256}")


def _emit_restore(result: RestoreResult, *, json_output: bool) -> None:
    if json_output:
        _emit(result)
        return
    typer.echo(f"Restored: {result.target_root}")
    typer.echo(f"Entries: {result.restored_entries}")
    message = (
        "Search index rebuilt: yes" if result.search_index_rebuilt else "Search index rebuilt: no"
    )
    typer.echo(message)


def register_alpha_commands(app: typer.Typer) -> tuple[Callable[..., None], ...]:
    """Attach sample-vault, run, verify, backup, export, and restore commands."""

    @app.command("sample-vault")
    def sample_vault_command(
        output: Annotated[Path, typer.Argument(file_okay=False, resolve_path=True)],
        force: Annotated[bool, typer.Option("--force")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Create a deterministic synthetic cross-file vault."""

        destination = create_sample_vault(output, force=force)
        if json_output:
            _emit({"sample_vault": str(destination)})
        else:
            typer.echo(f"Sample vault: {destination}")

    @app.command("run")
    def run_command(
        task_path: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
        ],
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    ) -> None:
        """Run one bounded deterministic task and record terminal evidence."""

        result = run_task(task_path, home=home)
        _emit(result)
        if result.status is not RunStatus.SUCCEEDED:
            raise typer.Exit(code=1)

    @app.command("verify")
    def verify_command(
        run_id: Annotated[str, typer.Argument()],
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
        render: Annotated[bool | None, typer.Option("--render/--no-render")] = None,
    ) -> None:
        """Independently revalidate one prior task run by ID."""

        result = verify_task_run(run_id, home=home, render=render)
        _emit(result)
        if not result.valid:
            raise typer.Exit(code=1)

    @app.command("backup")
    def backup_command(
        output: Annotated[Path, typer.Argument(file_okay=True, resolve_path=True)],
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Create a verified durable-state backup excluding rebuildable indexes."""

        _emit_archive(
            create_archive(output, home=home, kind="backup"),
            json_output=json_output,
        )

    @app.command("export")
    def export_command(
        output: Annotated[Path, typer.Argument(file_okay=True, resolve_path=True)],
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Create a portable verified durable-state export."""

        _emit_archive(
            create_archive(output, home=home, kind="portable-export"),
            json_output=json_output,
        )

    @app.command("restore")
    def restore_command(
        archive_path: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
        ],
        home: Annotated[Path, typer.Option("--home", file_okay=False, resolve_path=True)],
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Restore durable state into an empty home and rebuild search indexes."""

        _emit_restore(
            restore_archive(archive_path, home=home),
            json_output=json_output,
        )

    return (
        sample_vault_command,
        run_command,
        verify_command,
        backup_command,
        export_command,
        restore_command,
    )
