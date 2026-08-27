"""Desktop application and diagnostic console over Archiv's bounded services.

The console derives its command and argument schema from the installed Typer
application, never from a hand-maintained copy.  It builds argument vectors
for exactly one command at a time, executes them with ``shell=False``, keeps
the complete output of the current run, and only opens artifacts or preserved
sources after Archiv's existing bounded validators accept them.
"""

from __future__ import annotations

from archiv.ui.argv import build_argv, quoted_invocation
from archiv.ui.console_schema import (
    ConsoleCommand,
    ConsoleParameter,
    ConsoleSchema,
    ParameterKind,
    collect_console_schema,
)
from archiv.ui.errors import UiError
from archiv.ui.outputs import OpenableArtifact, RunOutputs, inspect_run_output
from archiv.ui.runner import ConsoleRunner, RunOutcome

__all__ = [
    "ConsoleCommand",
    "ConsoleParameter",
    "ConsoleRunner",
    "ConsoleSchema",
    "OpenableArtifact",
    "ParameterKind",
    "RunOutcome",
    "RunOutputs",
    "UiError",
    "build_argv",
    "collect_console_schema",
    "inspect_run_output",
    "quoted_invocation",
]
