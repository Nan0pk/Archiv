"""A disposable, resource-limited process for the complete Excel reader."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from archiv.contracts import NormalizedDocument
from archiv.hashing import sha256_file
from archiv.ingestion.limits import (
    MAX_CPU_SECONDS,
    MAX_EXPANDED_BYTES,
    MAX_MEMORY_BYTES,
    MAX_TIMEOUT_SECONDS,
    NativeResourceLimitError,
)

# Limits are applied before importing the document reader or any third-party parser.
# No preexec_fn is needed, so this remains safe when called from a worker thread.
_WORKER_BOOTSTRAP = """
import resource
import sys
cpu, memory, output = map(int, sys.argv[1:])
resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
resource.setrlimit(resource.RLIMIT_FSIZE, (output, output))
from archiv.ingestion.xlsx_process import worker_main
worker_main()
"""


def read_in_worker(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    cell_limit: int,
    node_limit: int,
) -> NormalizedDocument:
    if os.name != "posix":
        raise NativeResourceLimitError("XLSX processing limits require a POSIX worker")
    request = json.dumps(
        {
            "path": str(path.resolve()),
            "digest": digest,
            "source_name": source_name,
            "media_type": media_type,
            "cell_limit": cell_limit,
            "node_limit": node_limit,
        }
    )
    command = [
        sys.executable,
        "-c",
        _WORKER_BOOTSTRAP,
        str(MAX_CPU_SECONDS),
        str(MAX_MEMORY_BYTES),
        str(MAX_EXPANDED_BYTES),
    ]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostic:
        try:
            completed = subprocess.run(
                command,
                input=request.encode("utf-8"),
                stdout=output,
                stderr=diagnostic,
                timeout=MAX_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            # subprocess.run kills and waits for the child before raising.
            raise NativeResourceLimitError(
                f"XLSX processing time limit exceeded ({MAX_TIMEOUT_SECONDS} seconds)"
            ) from error
        if completed.returncode < 0:
            if -completed.returncode == signal.SIGXFSZ:
                raise NativeResourceLimitError(
                    f"XLSX normalized response size limit exceeded ({MAX_EXPANDED_BYTES} bytes)"
                )
            raise NativeResourceLimitError(
                f"XLSX worker terminated by signal {-completed.returncode}"
            )
        if completed.returncode != 0:
            diagnostic.seek(0)
            detail = diagnostic.read(500).decode("utf-8", errors="replace")
            if "EFBIG" in detail or "File too large" in detail or "file size limit" in detail:
                # A POSIX child that writes past RLIMIT_FSIZE may die by SIGXFSZ or
                # raise OSError(EFBIG); Python then exits 120 while flushing stdout.
                raise NativeResourceLimitError(
                    f"XLSX normalized response size limit exceeded ({MAX_EXPANDED_BYTES} bytes)"
                )
            if completed.returncode == 120:
                # CPython's exit status when it cannot flush its standard streams. The
                # child only writes its response, so a truncated response is the cause.
                raise NativeResourceLimitError(
                    "XLSX worker could not write its complete result; "
                    "the response size limit was probably reached"
                )
            raise ValueError(f"XLSX worker failed (exit {completed.returncode}): {detail}")
        output.seek(0)
        payload = output.read(MAX_EXPANDED_BYTES + 1)
        if len(payload) > MAX_EXPANDED_BYTES:
            raise NativeResourceLimitError("XLSX normalized response size limit exceeded")
        response_value: object = json.loads(payload.decode("utf-8"))
    if not isinstance(response_value, dict):
        raise ValueError("XLSX worker response must be an object")
    response = cast(dict[str, object], response_value)
    if response.get("status") == "resource_limit":
        raise NativeResourceLimitError(str(response.get("error", "XLSX worker resource limit")))
    if response.get("status") == "error":
        raise ValueError(str(response.get("error", "XLSX worker parsing error")))
    if response.get("status") != "succeeded":
        raise ValueError("XLSX worker response has no successful status")
    document = NormalizedDocument.model_validate(response.get("document"))
    if document.object_sha256 != digest:
        raise ValueError("XLSX worker response does not match the requested object hash")
    return document


def _emit(response: dict[str, Any]) -> None:
    """Write UTF-8 bytes, independent of the child's locale and stdout encoding."""

    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def worker_main() -> None:
    """Parse without storage access; exchange only JSON, never executable objects."""

    from archiv.ingestion import normalize_xlsx as reader

    request = cast(dict[str, object], json.loads(sys.stdin.buffer.read(64 * 1024)))
    reader.MAX_CELL_POSITIONS = int(cast(int, request["cell_limit"]))
    reader.MAX_XML_NODES = int(cast(int, request["node_limit"]))
    path = Path(cast(str, request["path"]))
    digest = cast(str, request["digest"])
    try:
        # The parent hashed this file before starting the child. Hashing it here makes
        # the hash carried by the returned document describe bytes that were read.
        if sha256_file(path) != digest:
            raise ValueError("XLSX source changed before the worker read it")
        document = reader.read_xlsx(
            path,
            digest,
            source_name=cast(str, request["source_name"]),
            media_type=cast(str, request["media_type"]),
        )
        response: dict[str, Any] = {
            "status": "succeeded",
            "document": document.model_dump(mode="json"),
        }
    except (MemoryError, NativeResourceLimitError) as error:
        response = {
            "status": "resource_limit",
            "error": f"XLSX worker: {type(error).__name__}: {error}",
        }
    except Exception as error:
        response = {"status": "error", "error": f"{type(error).__name__}: {error}"}
    try:
        _emit(response)
    except MemoryError:
        _emit(
            {
                "status": "resource_limit",
                "error": "XLSX worker ran out of memory while writing its result",
            }
        )
