"""`archiv setup`: one guided pass that chooses the library, the documents and the model."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from archiv.storage.layout import ArchivLayout, MissingLibraryError, default_home, remember_home
from archiv.ui.product import (
    DesktopState,
    configure_model,
    detect_loopback_models,
    gguf_hint,
    load_state,
    save_state,
    verify_model,
)


def _default_home() -> Path:
    try:
        return ArchivLayout.resolve().root
    except MissingLibraryError:  # offer the standard place instead
        return default_home().resolve()


def _choose_model(home: Path) -> str:
    """Prompt until a model is saved or skipped; return a one-line outcome."""

    while True:
        found = detect_loopback_models()
        typer.echo("")
        if found:
            typer.echo("Models found on this computer:")
            for number, (endpoint, server, model) in enumerate(found, start=1):
                typer.echo(f"  {number}. {model}  ({server}, {endpoint})")
        else:
            typer.echo(
                "No model server found on this computer (looked for Ollama, LM Studio and "
                "llama.cpp). Searching works without one; only `ask` and `report` need it."
            )
        typer.echo("  m. Enter a server address by hand")
        typer.echo("  f. I have a model file (.gguf)")
        typer.echo("  r. Look again")
        typer.echo("  s. Skip for now")
        answer = typer.prompt("Choose", default="1" if found else "s").strip().lower()
        if answer == "s":
            return "No model chosen. Run `archiv setup` again whenever you have one."
        if answer == "r":
            continue
        if answer == "f":
            file = Path(typer.prompt("Path to the model file")).expanduser()
            typer.echo(gguf_hint(file) if file.is_file() else f"{file} does not exist.")
            continue
        if answer == "m":
            endpoint = typer.prompt("Server address", default="http://127.0.0.1:11434")
            model = typer.prompt("Model name")
        elif answer.isdigit() and 1 <= int(answer) <= len(found):
            endpoint, _server, model = found[int(answer) - 1]
        else:
            typer.echo(f"{answer!r} is not one of the choices.")
            continue
        try:
            configure_model(home, endpoint, model)
        except ValueError as error:
            typer.echo(f"Not saved: {error}.")
            continue
        typer.echo(f"Checking that {model} answers…")
        ok, message = verify_model(home)
        if ok:
            return f"Model ready: {message}"
        typer.echo(f"Saved, but the check failed: {message}")
        if not typer.confirm("Keep this model anyway?", default=False):
            continue
        return f"Model saved but not answering yet: {message}"


def register_setup_command(app: typer.Typer) -> None:
    @app.command("setup")
    def setup_command(
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, help="Where Archiv keeps its library."),
        ] = None,
        folder: Annotated[
            Path | None,
            typer.Option("--folder", file_okay=False, help="A folder of documents to add now."),
        ] = None,
    ) -> None:
        """Guided first run: choose the library location, add documents, pick a model."""

        typer.echo("Archiv setup. Nothing here leaves this computer.\n")
        if home is None:
            typer.echo("1. Where should Archiv keep its library? It holds a preserved copy of")
            typer.echo("   every document plus the search index, so pick a disk with room.")
            home = Path(typer.prompt("   Library folder", default=str(_default_home())))
        layout = ArchivLayout.resolve(home)
        layout.ensure()
        remember_home(layout.root)
        typer.echo(f"   Library: {layout.root} (remembered for every command)")

        if folder is None:
            typer.echo(
                "\n2. Which folder of documents should be added? Originals are never changed."
            )
            answer = typer.prompt("   Documents folder (Enter to skip)", default="").strip()
            folder = Path(answer) if answer else None
        added = "No documents added yet. Use `archiv add <folder>` later."
        if folder is not None:
            folder = folder.expanduser().resolve()
            if not folder.is_dir():
                typer.echo(f"   {folder} is not a folder; skipping. Use `archiv add` later.")
            else:
                from archiv.cli import app as cli_app  # imported here: cli imports this module

                typer.echo(f"   Adding {folder} …")
                code = cli_app(
                    ["add", str(folder), "--home", str(layout.root)], standalone_mode=False
                )
                state = load_state(layout.root)
                if folder not in state.folders:
                    save_state(
                        DesktopState(layout.root, (*state.folders, folder), state.recent_questions)
                    )
                added = (
                    f"Added from {folder} (counts above)."
                    if not code
                    else f"Some files in {folder} could not be added; `archiv status` lists them."
                )

        typer.echo("\n3. Which AI model should answer questions? It must run on this computer.")
        model = _choose_model(layout.root)

        typer.echo("\nDone.")
        typer.echo(f"  Library:   {layout.root}")
        typer.echo(f"  Documents: {added}")
        typer.echo(f"  {model}")
        typer.echo('Try: archiv find "a phrase"  ·  archiv ask "a question"  ·  archiv ui')
