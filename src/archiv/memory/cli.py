"""Command line for the institutional memory: `archiv memory …`."""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from archiv.memory.build import build_memory
from archiv.memory.model import Complete, command_completer
from archiv.memory.vectors import Embedder, EmbedderUnavailable, FastEmbedder

memory_app = typer.Typer(
    no_args_is_help=True,
    help="Build a web of things and connections from the archive, and ask it questions.",
)

HomeOption = Annotated[Path | None, typer.Option("--home", file_okay=False)]
ModelCmdOption = Annotated[
    str | None,
    typer.Option(
        "--model-cmd",
        help="Command that reads a prompt on standard input and prints the answer. "
        "Defaults to $ARCHIV_MODEL_CMD.",
    ),
]
ModelLabelOption = Annotated[
    str,
    typer.Option(
        "--model-label", help="Name for the model behind --model-cmd; part of the cache key."
    ),
]


def _completer(model_cmd: str | None) -> Complete:
    command = model_cmd or os.environ.get("ARCHIV_MODEL_CMD")
    if not command:
        typer.echo(
            "No model command given. Pass --model-cmd or set ARCHIV_MODEL_CMD to a command "
            "that reads a prompt on standard input and prints the answer.",
            err=True,
        )
        raise typer.Exit(2)
    return command_completer(shlex.split(command))


def _embedder() -> Embedder | None:
    try:
        return FastEmbedder()
    except EmbedderUnavailable as error:
        typer.echo(f"Meaning vectors skipped. {error}", err=True)
        return None


@memory_app.command("build")
def build_command(
    model_label: ModelLabelOption,
    home: HomeOption = None,
    model_cmd: ModelCmdOption = None,
    max_chars: Annotated[int, typer.Option("--max-chars", min=200)] = 6000,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Read every document through the model and store what it mentions and how it connects."""
    summary = build_memory(
        home=home,
        complete=_completer(model_cmd),
        model_label=model_label,
        max_chars=max_chars,
        embedder=_embedder(),
    )
    if json_output:
        typer.echo(json.dumps(asdict(summary), indent=2))
        return
    typer.echo(
        f"Read {summary.documents} documents: {summary.model_calls} model calls, "
        f"{summary.cache_hits} answers reused from earlier runs, "
        f"{summary.failed_batches} batches the model did not answer usably.\n"
        f"The memory now holds {summary.things} things and {summary.relations} relations."
    )
