"""Open verified console artifacts through the operating system's handler.

Only ``xdg-open`` (Fedora/Linux desktops) is used, invoked as an argument
vector with ``shell=False``.  When no handler is installed the console fails
clearly instead of falling back to shell execution.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from archiv.ui.errors import UiError

OPEN_TIMEOUT_SECONDS = 30


def default_handler() -> str:
    """Return the desktop's default file handler or fail clearly."""

    handler = shutil.which("xdg-open")
    if handler is None:
        raise UiError(
            "no default file handler is available: install the xdg-utils package "
            "or open the verified path manually"
        )
    return handler


def open_with_default_handler(path: Path, *, handler: str | None = None) -> None:
    """Open one already-validated path with the OS default application."""

    resolved = path.expanduser()
    if not resolved.exists():
        raise UiError(f"cannot open a path that does not exist: {resolved}")
    opener = handler if handler is not None else default_handler()
    try:
        completed = subprocess.run(
            [opener, str(resolved)],
            shell=False,
            check=False,
            capture_output=True,
            timeout=OPEN_TIMEOUT_SECONDS,
        )
    except OSError as error:
        raise UiError(f"the default file handler could not start: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise UiError("the default file handler did not respond in time") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        message = f"the default file handler failed for {resolved}"
        if detail:
            message += f": {detail}"
        raise UiError(message)
