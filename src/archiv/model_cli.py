"""CLI sub-commands for explicit local model configuration and diagnostics."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from archiv.model_adapter import (
    ModelConfig,
    build_model_adapter,
    load_model_config,
    save_model_config,
)

model_app = typer.Typer(
    no_args_is_help=True, help="Manage local OpenAI-compatible model configuration."
)


def _emit_json(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


@model_app.command("status")
def model_status_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the currently configured local model adapter status."""

    config = load_model_config(home)
    if json_output:
        _emit_json(config)
        return

    typer.echo(f"Adapter: {config.adapter}")
    if config.adapter == "disabled":
        typer.echo("Status: disabled")
    else:
        typer.echo(f"Endpoint: {config.endpoint}")
        typer.echo(f"Model: {config.model}")
        typer.echo(f"API key env: {config.api_key_env or 'none'}")
        typer.echo(f"Timeout: {config.timeout_seconds}s")


@model_app.command("show")
def model_show_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Show the exact persisted model policy; absence means disabled."""

    _emit_json(load_model_config(home))


@model_app.command("configure")
def model_configure_command(
    endpoint: Annotated[
        str,
        typer.Option(
            "--endpoint",
            help="HTTP endpoint bound explicitly to loopback (e.g. http://127.0.0.1:11434).",
        ),
    ],
    model: Annotated[
        str,
        typer.Option("--model", help="Target model identifier (e.g. llama3)."),
    ],
    api_key_env: Annotated[
        str | None,
        typer.Option(
            "--api-key-env",
            help="Optional environment variable name containing the bearer token.",
        ),
    ] = None,
    timeout: Annotated[
        int,
        typer.Option("--timeout", min=1, max=3600, help="Request timeout in seconds."),
    ] = 120,
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Configure an OpenAI-compatible HTTP model server bound to loopback."""

    try:
        config = ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint=endpoint,
            model=model,
            api_key_env=api_key_env,
            timeout_seconds=timeout,
        )
        saved_path = save_model_config(config, home)
    except Exception as error:
        typer.echo(f"configuration failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    payload = {
        "schema_version": "1",
        "status": "configured",
        "config_path": str(saved_path),
        "config": config.model_dump(mode="json"),
    }
    if json_output:
        _emit_json(payload)
        return

    typer.echo(f"Configured local model: {config.model}")
    typer.echo(f"Endpoint: {config.endpoint}")
    typer.echo(f"Config saved to: {saved_path}")


@model_app.command("configure-loopback")
def model_configure_loopback_command(
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
    _emit_json({"config_path": str(path), "config": config.model_dump(mode="json")})


@model_app.command("test")
def model_test_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Test connectivity to the configured local model server."""

    config = load_model_config(home)
    if config.adapter == "disabled":
        payload = {
            "schema_version": "1",
            "status": "disabled",
            "message": (
                "Model adapter is disabled; configure a loopback endpoint to test connectivity."
            ),
        }
        if json_output:
            _emit_json(payload)
        else:
            typer.echo("Model adapter is disabled.")
        raise typer.Exit(code=1)

    start_time = time.monotonic()
    adapter = build_model_adapter(config)
    try:
        response = adapter.complete("Respond with the single word PONG.")
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        success_payload: dict[str, object] = {
            "schema_version": "1",
            "status": "succeeded",
            "latency_ms": duration_ms,
            "endpoint": config.endpoint,
            "model": config.model,
            "response": response.strip(),
        }
        if json_output:
            _emit_json(success_payload)
            return
        typer.echo(f"Connectivity test succeeded ({duration_ms} ms)")
        typer.echo(f"Endpoint: {config.endpoint}")
        typer.echo(f"Model: {config.model}")
    except Exception as error:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        failed_payload: dict[str, object] = {
            "schema_version": "1",
            "status": "failed",
            "latency_ms": duration_ms,
            "endpoint": config.endpoint,
            "model": config.model,
            "error": str(error),
        }
        if json_output:
            _emit_json(failed_payload)
        else:
            typer.echo(f"Connectivity test failed ({duration_ms} ms): {error}", err=True)
        raise typer.Exit(code=1) from error


@model_app.command("disable")
def model_disable_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Disable local model integration."""

    config = ModelConfig(adapter="disabled")
    saved_path = save_model_config(config, home)
    payload = {
        "schema_version": "1",
        "status": "disabled",
        "config_path": str(saved_path),
        "config": config.model_dump(mode="json"),
    }
    if json_output:
        _emit_json(payload)
        return

    typer.echo(f"Model integration disabled. Config saved to: {saved_path}")
