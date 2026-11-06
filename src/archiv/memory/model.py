"""The only model surface: a prompt goes in, text comes out.

The project holds no provider code. A caller supplies a command; the prompt is written to
its standard input and its standard output is the answer.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

Complete = Callable[[str], str]


def command_completer(argv: list[str], *, timeout_s: int = 900) -> Complete:
    """Return a `Complete` that runs `argv` once per prompt."""

    def complete(prompt: str) -> str:
        result = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, check=True, timeout=timeout_s
        )
        return result.stdout

    return complete
