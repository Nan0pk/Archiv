"""CLI registration for offline-alpha lifecycle commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from archiv.archive import create_archive, restore_archive
from archiv.contracts import RunStatus
from archiv.model_adapter import ModelConfig, load_model_config, save_model_config
from archiv.sample_vault import create_sample_vault
from archiv.tasks import run_task, verify_task_run


def _emit(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


def register_alpha_commands(app: typer.Typer) -> tuple[Callable[..., None], ...]:
    """Attach task, model, sample-vault, and durable-state commands."""

    model_app = typer.Typer(help="Configure an explicit local model adapter.")
    app.add_typer(model_app, name="model")

    @model_app.command("show")
    def model_show(
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    ) -> None:
        """Show the exact persisted model policy; absence means disabled."""

        _emit(load_model_config(home))

    @model_app.command("disable")
    def model_disable(
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    ) -> None:
        """Persist an explicit no-model policy."""

        config = ModelConfig()
        path = save_model_config(config, home)
        _emit({"config_path": str(path), "config": config.model_dump(mode="json")})

    @model_app.command("configure-loopback")
    def model_configure_loopback(
        endpoint: Annotated[str, typer.Option("--endpoint")],
        model: Annotated[str, typer.Option("--model")],
        api_key_env: Annotated[str | None, typer.Option("--api-key-env")] = None,
        timeout_seconds: Annotated[int, typer.Option("--timeout", min=1, max=3600)] = 120,
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    ) -> None:
        """Configure a loopback-only OpenAI-compatible endpoint without fallback."""

        config = ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint=endpoint,
            model=model,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
        )
        path = save_model_config(config, home)
        _emit({"config_path": str(path), "config": config.model_dump(mode="json")})

    @app.command("sample-vault")
    def sample_vault_command(
        output: Annotated[Path, typer.Argument(file_okay=False, resolve_path=True)],
        force: Annotated[bool, typer.Option("--force")] = False,
    ) -> None:
        """Create a deterministic synthetic cross-file vault."""

        _emit({"sample_vault": str(create_sample_vault(output, force=force))})

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
    ) -> None:
        """Create a verified durable-state backup excluding rebuildable indexes."""

        _emit(create_archive(output, home=home, kind="backup"))

    @app.command("export")
    def export_command(
        output: Annotated[Path, typer.Argument(file_okay=True, resolve_path=True)],
        home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
    ) -> None:
        """Create a portable verified durable-state export."""

        _emit(create_archive(output, home=home, kind="portable-export"))

    @app.command("restore")
    def restore_command(
        archive_path: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
        ],
        home: Annotated[Path, typer.Option("--home", file_okay=False, resolve_path=True)],
    ) -> None:
        """Restore durable state into an empty home and rebuild search indexes."""

        _emit(restore_archive(archive_path, home=home))

    return (
        model_show,
        model_disable,
        model_configure_loopback,
        sample_vault_command,
        run_command,
        verify_command,
        backup_command,
        export_command,
        restore_command,
    )
