"""Wire the minimal local test console into the Archiv CLI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

# ``import tkinter`` fails with ``name="tkinter"`` when the Python-level package
# is absent, but with ``name="_tkinter"`` when only the compiled extension is
# missing.  The second case is the common one: Debian/Ubuntu without
# ``python3-tk`` and source builds without Tcl/Tk headers both ship the pure
# Python package while omitting the C extension.  Both must produce the same
# actionable message instead of an unhandled traceback.
_TKINTER_MODULE_NAMES = frozenset({"tkinter", "_tkinter"})


def register_ui_command(app: typer.Typer) -> Callable[..., None]:
    """Attach the bounded local test-console command."""

    @app.command("ui")
    def ui_command(
        home: Annotated[
            Path | None,
            typer.Option(
                "--home",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Archiv home. Defaults to ARCHIV_HOME or the user data directory.",
            ),
        ] = None,
    ) -> None:
        """Open the minimal local test console over all Archiv commands."""

        try:
            from archiv.ui.tk_console import launch_console
        except ModuleNotFoundError as error:
            if error.name not in _TKINTER_MODULE_NAMES:
                raise
            typer.echo(
                "ui failed: the desktop UI dependency is unavailable; install the "
                "python3-tkinter package to use the test console",
                err=True,
            )
            raise typer.Exit(code=1) from error
        exit_code = launch_console(home=home)
        if exit_code != 0:
            raise typer.Exit(code=exit_code)

    return ui_command
