"""Read a legacy .ppt our own reader rejects as malformed, by converting it with LibreOffice.

Used only when ``normalize_ppt`` refuses a file because a record extends past its
container: LibreOffice tolerates that damage and our reader does not. The conversion runs
inside bubblewrap with no network, a read-only view of the system, its own throwaway home
and profile, and declared CPU, memory and time limits. Without ``bwrap`` or LibreOffice the
original rejection stands. The converted .pptx is a temporary derivative, never stored as an
original; every segment it yields carries ``origin: libreoffice_conversion``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import cache
from pathlib import Path

from archiv.contracts import NormalizedDocument, NormalizedSegment
from archiv.ingestion.limits import check_input
from archiv.ingestion.normalize_office import normalize_pptx
from archiv.ingestion.sandbox import SANDBOX_ARGUMENTS, executable_binds, sandbox_environment

# Measured on 33 real rejected files: at most 10.1 CPU seconds and 971 MB resident.
# LibreOffice reserves far more address space than it uses; below 4 GiB of address space
# it failed to start threads on 15 of the 33 (2 GiB) or on every file (1 GiB).
LIBREOFFICE_CPU_SECONDS = 60
LIBREOFFICE_ADDRESS_SPACE_BYTES = 4 * 1024 * 1024 * 1024
LIBREOFFICE_WALL_SECONDS = 180

ORIGIN = "libreoffice_conversion"
FALLBACK_REASON = "PPT record extends past its container"


def _limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (LIBREOFFICE_CPU_SECONDS, LIBREOFFICE_CPU_SECONDS))
    resource.setrlimit(
        resource.RLIMIT_AS, (LIBREOFFICE_ADDRESS_SPACE_BYTES, LIBREOFFICE_ADDRESS_SPACE_BYTES)
    )


def _tools() -> tuple[str, str] | None:
    office = shutil.which("soffice") or shutil.which("libreoffice")
    bubblewrap = shutil.which("bwrap")
    if os.name != "posix" or office is None or bubblewrap is None:
        return None
    return office, bubblewrap


def libreoffice_fallback_available() -> bool:
    return _tools() is not None


def convert_rejected_ppt(
    path: Path,
    digest: str,
    *,
    source_name: str,
    media_type: str,
    rejection: ValueError,
) -> NormalizedDocument:
    """Return the converted file's text, or raise ``rejection`` (with the reason) on any failure."""
    tools = _tools()
    if tools is None:
        raise ValueError(
            f"{rejection}; LibreOffice and bubblewrap (bwrap) are both needed to read it anyway"
        ) from rejection
    office, bubblewrap = tools
    with tempfile.TemporaryDirectory(prefix="archiv-ppt-") as temporary:
        work = Path(temporary).resolve()
        for name in ("home", "profile", "out"):
            (work / name).mkdir()
        source = work / "in.ppt"
        shutil.copyfile(path, source)
        command = [
            bubblewrap,
            *SANDBOX_ARGUMENTS,
            *executable_binds(office),
            # Re-exposes the work folder even when it lies under a folder just hidden.
            "--bind",
            str(work),
            str(work),
            "--chdir",
            str(work),
            "--",
            office,
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            f"-env:UserInstallation=file://{(work / 'profile').as_posix()}",
            "--convert-to",
            "pptx",
            "--outdir",
            str(work / "out"),
            str(source),
        ]
        environment = sandbox_environment(str(work / "home"))
        environment["GSETTINGS_BACKEND"] = "memory"
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=LIBREOFFICE_WALL_SECONDS,
                preexec_fn=_limits,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError(
                f"{rejection}; LibreOffice did not finish within {LIBREOFFICE_WALL_SECONDS} s"
            ) from error
        converted = work / "out" / "in.pptx"
        if completed.returncode != 0 or not converted.is_file():
            detail = " ".join((completed.stderr or completed.stdout).split())[-300:]
            raise ValueError(
                f"{rejection}; LibreOffice could not convert it (exit {completed.returncode}): "
                f"{detail or 'no diagnostic'}"
            ) from rejection
        # LibreOffice's output from a hostile input is itself untrusted: same input and zip limits.
        check_input(converted)
        document = normalize_pptx(converted, digest, source_name=source_name, media_type=media_type)
    return NormalizedDocument(
        object_sha256=digest,
        media_type=media_type,
        kind="ppt",
        source_name=source_name,
        segments=[
            NormalizedSegment(locator={**segment.locator, "origin": ORIGIN}, text=segment.text)
            for segment in document.segments
        ],
        tables=document.tables,
        metadata={
            "processor": "archiv.legacy-office-ppt",
            "converted_by": "LibreOffice, sandboxed with bubblewrap, no network",
            "converted_because": str(rejection),
            "libreoffice_version": _libreoffice_version(office, bubblewrap),
            "macros_executed": False,
        },
    )


@cache
def _libreoffice_version(office: str, bubblewrap: str) -> str:
    """LibreOffice's own version line, read inside the same sandbox; recorded so changed text
    after a LibreOffice upgrade can be explained."""
    with tempfile.TemporaryDirectory(prefix="archiv-ppt-") as temporary:
        home = Path(temporary).resolve()
        completed = subprocess.run(
            [
                bubblewrap,
                *SANDBOX_ARGUMENTS,
                *executable_binds(office),
                "--bind",
                str(home),
                str(home),
                "--",
                office,
                "--version",
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=sandbox_environment(str(home)),
            timeout=60,
            preexec_fn=_limits,
        )
    return completed.stdout.strip()[:200] or "unknown"
