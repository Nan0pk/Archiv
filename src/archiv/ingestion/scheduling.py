"""Which file a folder add starts next, so that big files do not stall it or exhaust memory.

Measured on a mixed collection (old Word files, PDFs, scanned images, spreadsheets):

- Starting files in path order left the machine idle at the end. One scanned PDF needed 32
  seconds, it happened to be near the end of the list, and for most of those seconds one
  worker was busy and the rest waited. Starting the biggest files first finished the same
  set 15 to 25 percent sooner, with 6.3 to 6.6 of 24 cores busy instead of 4.6 to 4.7.
- A few files take far more memory than their size suggests. In a real add, one 27 MB
  spreadsheet took 2.9 GB for the whole run (the worker, and the copy of the result that
  travels back to the main process) and two together took 5.5 GB. A 12 MB PDF took 3.0 GB
  while a 14 MB scanned PDF took 131 MB. Eight of the big ones at once ran a test out of
  memory, which is a different failure from a slow add.

So this module orders files by how much work they probably are, keeps at most a few big
files running at once, and does not start another file while memory is short.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile, is_zipfile

# A file counts as big from this many bytes. For zip-based formats (docx, xlsx, pptx, ods)
# the size once unpacked is used, because a few megabytes can unpack to hundreds.
BIG_FILE_BYTES = 8 * 1024 * 1024
# From this cost a file runs in a process of its own that ends with the file, so the memory
# it took goes back to the system. A long-lived worker keeps its high-water mark: in a
# measured run eight such workers sat at 4.9 GB with nothing big running. Smaller files cost
# a few milliseconds and never grow a worker much, so they share long-lived workers.
SIZABLE_BYTES = 1024 * 1024
MAX_BIG_IN_FLIGHT = 2
# Memory one big file may take in a real add (measured: 2.9 GB for one, 5.5 GB for two).
BIG_FILE_ROOM_MB = 3072
# And one sizable file that is not big. Measured peaks: 90 to 220 MB for most, 515 MB for one
# presentation and 745 MB for one 6 MB PDF. The reserve covers the usual case; the floor above
# covers the odd one that goes past it.
SIZABLE_FILE_ROOM_MB = 400
# Do not start another file while less than this is available.
MEMORY_FLOOR_MB = 3072
# Zip-based files smaller than this cannot unpack to much (the expansion limit is 200 times).
_ZIP_CHECK_FROM_BYTES = 1024 * 1024

# What the last pool run did, for tests and for measuring.
STATS: dict[str, float] = {"held_back_seconds": 0.0, "max_in_flight": 0, "max_big_in_flight": 0}


def cost_bytes(path: Path) -> int:
    """Roughly how much work a file is: its size, or its unpacked size for zip-based formats."""
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size < _ZIP_CHECK_FROM_BYTES:
        return size
    try:
        if is_zipfile(path):
            with ZipFile(path) as archive:
                return max(size, sum(member.file_size for member in archive.infolist()))
    except (BadZipFile, OSError, ValueError):
        pass
    return size


def rank_for_start(candidates: list[Path]) -> list[tuple[Path, int]]:
    """Every candidate with its cost, the biggest first (ties in path order)."""
    costed = [(path, cost_bytes(path)) for path in candidates]
    return sorted(costed, key=lambda item: (-item[1], str(item[0])))


def is_big(cost: int) -> bool:
    return cost >= BIG_FILE_BYTES


def is_sizable(cost: int) -> bool:
    return cost >= SIZABLE_BYTES


def reserve_mb(cost: int) -> int:
    """Memory held back for a file while it runs, because it has not grown yet when it starts.

    Looking at the memory in use now is not enough: eight files started in the same second
    all look harmless and then grow together. That is how a measured run reached 6.5 GB.
    """
    if is_big(cost):
        return BIG_FILE_ROOM_MB
    return SIZABLE_FILE_ROOM_MB if is_sizable(cost) else 0


def pick_next(
    pending: list[tuple[Path, int]],
    in_flight: int,
    big_in_flight: int,
    available_mb: int | None,
    reserved_mb: int = 0,
) -> int | None:
    """Index in `pending` of the file to start now, or None to wait.

    `reserved_mb` is what the files already running may still grow into (see `reserve_mb`).
    With nothing running the first file always starts, so an add can never stall. A big file
    that must wait does not hold up the small ones behind it; it starts once there is room.
    """
    if not pending:
        return None
    if in_flight == 0:
        return 0
    headroom = None if available_mb is None else available_mb - reserved_mb
    for index, (_path, cost) in enumerate(pending):
        if is_big(cost):
            if big_in_flight >= MAX_BIG_IN_FLIGHT:
                continue
            if headroom is not None and headroom < MEMORY_FLOOR_MB + BIG_FILE_ROOM_MB:
                continue
        elif headroom is not None and headroom < MEMORY_FLOOR_MB:
            continue
        return index
    return None


def _read_mb(path: Path, key: str) -> int | None:
    try:
        for line in path.read_text().splitlines():
            name, _, rest = line.partition(":")
            if name == key:
                return int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def available_memory_mb(
    proc_root: Path = Path("/proc"), cgroup_root: Path = Path("/sys/fs/cgroup")
) -> int | None:
    """Memory this process can still use, in MB, or None if it cannot be told.

    The smaller of what the whole machine reports as available and, if this process runs
    under a memory limit (a systemd scope, a container), the room left under the tightest
    limit up the chain. In a limited group only real memory counts against the limit, not
    the file cache: the cache is given back when needed and counting it would stop an add
    that is in no danger.
    """
    host = _read_mb(proc_root / "meminfo", "MemAvailable")
    if host is None:
        return None
    room = host
    try:
        own = next(
            line[3:].strip()
            for line in (proc_root / "self" / "cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
    except (OSError, StopIteration):
        return room
    group = Path(own)
    while True:
        directory = cgroup_root / str(group).lstrip("/")
        try:
            limit_text = (directory / "memory.max").read_text().strip()
        except OSError:
            limit_text = "max"
        if limit_text != "max":
            try:
                used = 0
                for line in (directory / "memory.stat").read_text().splitlines():
                    name, _, value = line.partition(" ")
                    if name in ("anon", "shmem"):
                        used += int(value)
                room = min(room, (int(limit_text) - used) // (1024 * 1024))
            except (OSError, ValueError):
                pass
        if str(group) in ("/", "."):
            return max(room, 0)
        group = group.parent
