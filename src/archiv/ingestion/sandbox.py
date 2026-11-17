"""Bubblewrap arguments shared by every external tool Archiv runs on untrusted files."""

from __future__ import annotations

import os

# No network, IPC, process or host-name sharing; the system read-only; and the folders that
# hold the user's session sockets (/run), files and collections (/home, /mnt, /media) hidden.
# Callers bind their own input and work folders after these, which re-exposes them.
SANDBOX_ARGUMENTS = (
    "--unshare-net",
    "--unshare-ipc",
    "--unshare-pid",
    "--unshare-uts",
    "--die-with-parent",
    "--new-session",
    "--ro-bind",
    "/",
    "/",
    "--dev",
    "/dev",
    "--proc",
    "/proc",
    "--tmpfs",
    "/tmp",
    "--tmpfs",
    "/var/tmp",
    "--tmpfs",
    "/run",
    "--tmpfs",
    "/home",
    "--tmpfs",
    "/mnt",
    "--tmpfs",
    "/media",
)

# Passed through when set; nothing that points at the user's session or credentials.
PASSED_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TESSDATA_PREFIX", "OMP_THREAD_LIMIT")


def sandbox_environment(home: str) -> dict[str, str]:
    environment = {key: os.environ[key] for key in PASSED_ENVIRONMENT if key in os.environ}
    environment["HOME"] = home
    return environment
