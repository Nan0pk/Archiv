"""A folder add starts the biggest files first, runs few big files at once, and waits for memory.

Measured before this change: starting files in path order left most workers idle while one
slow scanned PDF finished at the end of the list; and eight big spreadsheets or text-heavy
PDFs at once needed more than 7 GB, which is what a memory limit then killed.
"""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import threading
import time
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from archiv import user_cli
from archiv.hashing import sha256_file
from archiv.ingestion import scheduling
from archiv.ingestion.scheduling import (
    available_memory_mb,
    cost_bytes,
    pick_next,
    rank_for_start,
)

MB = 1024 * 1024


def _files(folder: Path, sizes: dict[str, int]) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, size in sizes.items():
        path = folder / name
        path.write_text(("word " * (size // 5 + 1))[:size], encoding="utf-8")
        paths.append(path)
    return paths


def test_biggest_first_and_equal_sizes_in_path_order(tmp_path: Path) -> None:
    paths = _files(tmp_path, {"b.txt": 100, "a.txt": 100, "c.txt": 5000, "d.txt": 40})
    assert [p.name for p, _cost in rank_for_start(paths)] == ["c.txt", "a.txt", "b.txt", "d.txt"]


def test_a_zip_based_file_is_ranked_by_its_unpacked_size(tmp_path: Path) -> None:
    """A few megabytes on disk can unpack to hundreds; the stored size would miss that."""
    packed = tmp_path / "packed.xlsx"
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("noise.bin", os.urandom(1_200_000))  # does not compress
        archive.writestr("xl/sheet.xml", b"0" * (20 * MB))  # compresses to almost nothing
    assert packed.stat().st_size < 2 * MB
    assert cost_bytes(packed) >= 20 * MB
    assert scheduling.is_big(cost_bytes(packed))


def test_a_damaged_zip_falls_back_to_its_size(tmp_path: Path) -> None:
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04" + os.urandom(2 * MB))
    assert cost_bytes(broken) == broken.stat().st_size


def _pending(*sizes: int) -> list[tuple[Path, int]]:
    return [(Path(f"f{index}"), size) for index, size in enumerate(sizes)]


BIG = scheduling.BIG_FILE_BYTES + 1
PLENTY = 100_000


def test_the_first_file_always_starts_even_with_no_memory() -> None:
    assert pick_next(_pending(BIG, 10), in_flight=0, big_in_flight=0, available_mb=0) == 0


def test_nothing_starts_while_memory_is_short() -> None:
    short = scheduling.MEMORY_FLOOR_MB - 1
    assert pick_next(_pending(10, 10), in_flight=1, big_in_flight=0, available_mb=short) is None


def test_a_big_file_needs_extra_room_but_a_small_one_behind_it_may_start() -> None:
    enough_for_small = scheduling.MEMORY_FLOOR_MB + 10
    queue = _pending(BIG, 10)
    assert pick_next(queue, in_flight=1, big_in_flight=0, available_mb=enough_for_small) == 1
    roomy = scheduling.MEMORY_FLOOR_MB + scheduling.BIG_FILE_ROOM_MB
    assert pick_next(queue, in_flight=1, big_in_flight=0, available_mb=roomy) == 0


def test_no_more_than_the_allowed_number_of_big_files_run_at_once() -> None:
    queue = _pending(BIG, BIG, 10)
    full = scheduling.MAX_BIG_IN_FLIGHT
    assert pick_next(queue, in_flight=full, big_in_flight=full, available_mb=PLENTY) == 2
    assert pick_next(_pending(BIG), in_flight=full, big_in_flight=full, available_mb=PLENTY) is None


def test_running_big_files_keep_a_reserve_so_a_second_one_waits_even_when_memory_looks_free() -> (
    None
):
    """Memory in use now does not show what a file that has just started will grow into."""
    roomy = scheduling.MEMORY_FLOOR_MB + scheduling.BIG_FILE_ROOM_MB + 500
    queue = _pending(BIG, 10)
    assert pick_next(queue, 1, 0, roomy, reserved_mb=0) == 0
    one_big_running = scheduling.BIG_FILE_ROOM_MB
    assert (
        pick_next(queue, 1, 1, roomy, reserved_mb=one_big_running) == 1
    )  # the small one still fits
    nothing_fits = scheduling.MEMORY_FLOOR_MB + 100
    assert pick_next(_pending(10), 1, 1, nothing_fits, reserved_mb=one_big_running) is None


def test_the_reserve_depends_on_how_heavy_the_file_is() -> None:
    assert scheduling.reserve_mb(10) == 0
    assert scheduling.reserve_mb(scheduling.SIZABLE_BYTES) == scheduling.SIZABLE_FILE_ROOM_MB
    assert scheduling.reserve_mb(scheduling.BIG_FILE_BYTES) == scheduling.BIG_FILE_ROOM_MB


def test_unknown_memory_does_not_hold_anything_back() -> None:
    assert pick_next(_pending(BIG, 10), in_flight=3, big_in_flight=0, available_mb=None) == 0


def _fake_machine(
    root: Path, *, host_mb: int, own: str, groups: dict[str, tuple[str, int, int]]
) -> tuple[Path, Path]:
    """groups maps a cgroup path to (memory.max text, anon bytes, file-cache bytes)."""
    proc, cgroup = root / "proc", root / "cgroup"
    (proc / "self").mkdir(parents=True)
    (proc / "meminfo").write_text(f"MemTotal: 99999999 kB\nMemAvailable: {host_mb * 1024} kB\n")
    (proc / "self" / "cgroup").write_text(f"0::{own}\n")
    for path, (limit, anon, cache) in groups.items():
        directory = cgroup / path.lstrip("/")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory.max").write_text(f"{limit}\n")
        (directory / "memory.stat").write_text(f"anon {anon}\nfile {cache}\nshmem 0\n")
    return proc, cgroup


def test_without_a_memory_limit_the_machine_figure_is_used(tmp_path: Path) -> None:
    proc, cgroup = _fake_machine(
        tmp_path, host_mb=10_240, own="/a/b", groups={"/a/b": ("max", 0, 0), "/a": ("max", 0, 0)}
    )
    assert available_memory_mb(proc, cgroup) == 10_240


def test_a_limit_counts_real_memory_but_not_file_cache(tmp_path: Path) -> None:
    """The cache is given back when needed; counting it stops adds that are in no danger."""
    proc, cgroup = _fake_machine(
        tmp_path,
        host_mb=10_240,
        own="/a/b",
        groups={
            "/a/b": ("max", 0, 0),
            "/a": (
                str(8 * 1024 * MB),
                1024 * MB,
                5 * 1024 * MB,
            ),  # 8 GB limit, 1 GB real, 5 GB cache
        },
    )
    assert available_memory_mb(proc, cgroup) == 7 * 1024


def test_the_tightest_limit_up_the_chain_wins(tmp_path: Path) -> None:
    proc, cgroup = _fake_machine(
        tmp_path,
        host_mb=20_000,
        own="/a/b",
        groups={
            "/a/b": (str(6 * 1024 * MB), 4 * 1024 * MB, 0),
            "/a": (str(30 * 1024 * MB), 0, 0),
        },
    )
    assert available_memory_mb(proc, cgroup) == 2 * 1024


def test_when_memory_cannot_be_read_nothing_is_limited(tmp_path: Path) -> None:
    assert available_memory_mb(tmp_path / "nowhere", tmp_path / "nowhere") is None


def test_the_real_machine_figure_is_sensible() -> None:
    value = available_memory_mb()
    if value is not None:  # a platform without /proc cannot be told
        assert 0 <= value < 10_000_000


class _RecordingPool(ThreadPoolExecutor):
    """Stands in for the process pool so a test can see what was started and when."""

    started: list[Path] = []  # noqa: RUF012
    lanes: dict[str, str] = {}  # noqa: RUF012

    def __init__(self, max_workers: int | None = None, **options: Any) -> None:
        super().__init__(max_workers=max_workers)
        self._lane = "one-shot" if max_workers == 1 else "shared"

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        _RecordingPool.started.append(args[0])
        _RecordingPool.lanes[args[0].name] = self._lane
        return super().submit(fn, *args, **kwargs)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"running": 0, "max_running": 0, "max_big_running": 0, "big_running": 0}
    lock = threading.Lock()
    real_task = user_cli._prepare_candidate_task  # pyright: ignore[reportPrivateUsage]
    _RecordingPool.started = []
    _RecordingPool.lanes = {}

    def slow_task(candidate: Path, home: Path | None, rebuild: bool) -> Any:
        big = candidate.stat().st_size >= scheduling.BIG_FILE_BYTES
        with lock:
            state["running"] += 1
            state["big_running"] += big
            state["max_running"] = max(state["max_running"], state["running"])
            state["max_big_running"] = max(state["max_big_running"], state["big_running"])
        time.sleep(0.05)
        try:
            return real_task(candidate, home, rebuild)
        finally:
            with lock:
                state["running"] -= 1
                state["big_running"] -= big

    monkeypatch.setattr(user_cli, "ProcessPoolExecutor", _RecordingPool)
    monkeypatch.setattr(user_cli, "_prepare_candidate_task", slow_task)
    monkeypatch.setattr(scheduling, "available_memory_mb", lambda: PLENTY)
    return state


def _add(folder: Path, home: Path) -> list[str]:
    """Run the add, failing instead of hanging if the scheduler ever stalls."""
    outcome: list[Any] = []

    def run() -> None:
        outcome.append(
            user_cli._add_sources(folder, home=home, rebuild_derived=False)  # pyright: ignore[reportPrivateUsage]
        )

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive(), "the add stalled: nothing was started and nothing was running"
    results, _index, counts = outcome[0]
    assert counts.failed == 0
    return [r.object_sha256 for r in results]


def test_a_folder_add_starts_the_biggest_files_first_and_reports_in_path_order(
    tmp_path: Path, recording: dict[str, Any]
) -> None:
    _files(
        tmp_path / "src", {"a.txt": 300, "b.txt": 9000, "c.txt": 120, "d.txt": 5000, "e.txt": 700}
    )
    results = _add(tmp_path / "src", tmp_path / "home")
    assert [p.name for p in _RecordingPool.started] == ["b.txt", "d.txt", "e.txt", "a.txt", "c.txt"]
    in_path_order = [sha256_file(p) for p in sorted((tmp_path / "src").glob("*.txt"))]
    assert results == in_path_order
    assert scheduling.STATS["max_in_flight"] <= 5


def test_short_memory_runs_one_file_at_a_time_and_still_finishes(
    tmp_path: Path, recording: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduling, "available_memory_mb", lambda: 0)
    _files(tmp_path / "src", {f"n{i}.txt": 100 + i for i in range(6)})
    results = _add(tmp_path / "src", tmp_path / "home")
    assert len(results) == 6
    assert recording["max_running"] == 1
    assert scheduling.STATS["max_in_flight"] == 1
    assert scheduling.STATS["held_back_seconds"] > 0


def test_at_most_two_big_files_run_at_once_and_all_of_them_finish(
    tmp_path: Path, recording: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduling, "BIG_FILE_BYTES", 1000)
    sizes = {f"big{i}.txt": 2000 + i for i in range(5)} | {
        f"small{i}.txt": 50 + i for i in range(5)
    }
    _files(tmp_path / "src", sizes)
    results = _add(tmp_path / "src", tmp_path / "home")
    assert len(results) == 10
    assert recording["max_big_running"] <= scheduling.MAX_BIG_IN_FLIGHT
    assert scheduling.STATS["max_big_in_flight"] <= scheduling.MAX_BIG_IN_FLIGHT
    assert recording["max_running"] > 2  # the small files were not stuck behind the big ones


def test_a_single_file_does_not_use_the_scheduler(
    tmp_path: Path, recording: dict[str, Any]
) -> None:
    only = _files(tmp_path / "src", {"one.txt": 100})[0]
    results, _index, counts = user_cli._add_sources(  # pyright: ignore[reportPrivateUsage]
        only, home=tmp_path / "home", rebuild_derived=False
    )
    assert counts.failed == 0 and len(results) == 1
    assert _RecordingPool.started == []


def test_sizable_files_get_a_process_of_their_own_and_small_ones_share_workers(
    tmp_path: Path, recording: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long-lived worker keeps the memory of the biggest file it ever read."""
    monkeypatch.setattr(scheduling, "SIZABLE_BYTES", 1000)
    _files(
        tmp_path / "src", {"tiny1.txt": 50, "tiny2.txt": 60, "bulky1.txt": 4000, "bulky2.txt": 5000}
    )
    _add(tmp_path / "src", tmp_path / "home")
    assert _RecordingPool.lanes == {
        "tiny1.txt": "shared",
        "tiny2.txt": "shared",
        "bulky1.txt": "one-shot",
        "bulky2.txt": "one-shot",
    }


def test_a_prepared_file_sent_back_carries_no_text_but_the_search_still_finds_it(
    tmp_path: Path,
) -> None:
    """The text is on disk under derived/; sending it back too cost gigabytes for big files."""
    source = _files(tmp_path / "src", {"memo.txt": 200})[0]
    source.write_text("The harvest festival was held near the old mill.\n", encoding="utf-8")
    home = tmp_path / "home"
    _path, prepared, error = user_cli._prepare_candidate_task(  # pyright: ignore[reportPrivateUsage]
        source, home, False
    )
    assert error is None and prepared is not None
    assert prepared.normalized.segments == [] and prepared.normalized.tables == []
    assert prepared.normalized.kind == "txt"
    written = next((home / "derived").rglob("document.json")).read_text(encoding="utf-8")
    assert "harvest festival" in written


def test_real_processes_run_both_lanes_record_failures_and_leave_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other tests stand threads in for the pools; this one uses the real thing."""
    monkeypatch.setattr(scheduling, "SIZABLE_BYTES", 1000)
    source = tmp_path / "src"
    _files(source, {"small1.txt": 50, "small2.txt": 60, "bulky1.txt": 4000, "bulky2.txt": 5000})
    (source / "unreadable-small.txt").write_bytes(b"\xff\xfe broken \x80\x81")
    (source / "unreadable-bulky.txt").write_bytes(b"\xff\xfe " + b"broken \x80\x81 " * 200)
    results, _index, counts = user_cli._add_sources(  # pyright: ignore[reportPrivateUsage]
        source, home=tmp_path / "home", rebuild_derived=False
    )
    assert len(results) == 4  # the four readable files, in either lane
    assert counts.failed == 2  # one failure came back from each lane
    connection = sqlite3.connect(tmp_path / "home" / "archiv.sqlite3")
    try:
        recorded = connection.execute("SELECT COUNT(*) FROM ingestion_failures").fetchone()[0]
    finally:
        connection.close()
    assert recorded == 2
    assert multiprocessing.active_children() == []  # every single-use process was shut down
