"""Single-flight child process runner for the local test console.

Commands execute as argument vectors with ``shell=False``; no shell ever
parses console input.  Only one command runs at a time.  The complete merged
output of the current run is retained until the next run starts, up to a
deliberate hard bound that is honestly reported when hit.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from archiv.ui.errors import UiError

MAX_RETAINED_OUTPUT_BYTES = 5 * 1024 * 1024
_TRUNCATION_NOTICE = "\n[console retained-output limit reached; later output was discarded]\n"


def console_executable_argv(argv: Sequence[str]) -> list[str]:
    """Prefix console argv with the current interpreter's installed Archiv CLI."""

    return [sys.executable, "-m", "archiv.cli", *argv]


@dataclass(frozen=True)
class RunOutcome:
    """Terminal state of one finished console run."""

    argv: tuple[str, ...]
    started_at: str
    finished_at: str
    exit_code: int | None
    output: str
    output_truncated: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class _ActiveRun:
    argv: tuple[str, ...]
    started_at: str
    process: subprocess.Popen[bytes]
    reader: threading.Thread
    chunks: list[bytes] = field(default_factory=lambda: list[bytes]())


class ConsoleRunner:
    """Run one Archiv command at a time and retain its complete output."""

    def __init__(
        self,
        *,
        retain_limit: int = MAX_RETAINED_OUTPUT_BYTES,
        on_output: Callable[[bytes], None] | None = None,
    ) -> None:
        if retain_limit < 1024:
            raise ValueError("the console must retain at least 1024 bytes of output")
        self._retain_limit = retain_limit
        self._on_output = on_output
        self._active: _ActiveRun | None = None
        self._retained = 0
        self._truncated = False
        self._last: RunOutcome | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._active is not None

    @property
    def last_outcome(self) -> RunOutcome | None:
        return self._last

    def start(self, argv: Sequence[str], *, cwd: str | None = None) -> None:
        """Start one command; raises while another command is still running."""

        if not argv:
            raise UiError("the console cannot start an empty argument vector")
        if any(not part for part in argv):
            raise UiError("argument vector elements must be non-empty text")
        if any("\x00" in part for part in argv):
            raise UiError("argument vector elements must not contain NUL bytes")
        with self._lock:
            if self._active is not None:
                raise UiError("another command is still running; wait for it to finish")
            try:
                process = subprocess.Popen(
                    list(argv),
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                )
            except OSError as error:
                raise UiError(f"could not start the command: {error}") from error
            active = _ActiveRun(
                argv=tuple(argv),
                started_at=datetime.now(UTC).isoformat(),
                process=process,
                reader=threading.Thread(target=self._drain, args=(process,), daemon=True),
            )
            self._active = active
            active.reader.start()

    def _drain(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            active = self._active
        assert active is not None
        assert process.stdout is not None
        while True:
            chunk = process.stdout.readline()
            if not chunk:
                break
            with self._lock:
                if self._retained + len(chunk) <= self._retain_limit:
                    active.chunks.append(chunk)
                    self._retained += len(chunk)
                else:
                    self._truncated = True
            if self._on_output is not None:
                self._on_output(chunk)
        process.stdout.close()
        process.wait()
        finished_at = datetime.now(UTC).isoformat()
        with self._lock:
            output = b"".join(active.chunks).decode("utf-8", errors="replace")
            if self._truncated:
                output += _TRUNCATION_NOTICE
            self._last = RunOutcome(
                argv=active.argv,
                started_at=active.started_at,
                finished_at=finished_at,
                exit_code=process.returncode,
                output=output,
                output_truncated=self._truncated,
            )
            active.chunks = []
            self._retained = 0
            self._truncated = False
            self._active = None

    def wait(self, timeout: float | None = None) -> RunOutcome:
        """Block until the active run finishes and return its outcome."""

        with self._lock:
            active = self._active
        if active is not None:
            active.reader.join(timeout)
            if active.reader.is_alive():
                raise UiError("the command did not finish within the requested time")
        with self._lock:
            if self._last is None:
                raise UiError("no console run has finished yet")
            return self._last

    def terminate(self) -> None:
        """Politely stop the active run, killing it only if it ignores SIGTERM."""

        with self._lock:
            active = self._active
        if active is None:
            return
        active.process.terminate()
        try:
            active.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            active.process.kill()
            active.process.wait(timeout=5)
