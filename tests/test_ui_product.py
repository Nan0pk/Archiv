"""Desktop integration contracts exercised without requiring a display."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from archiv.storage.database import ArchivDatabase
from archiv.storage.layout import ArchivLayout
from archiv.ui.product import (
    DesktopState,
    ingestion_argv,
    list_documents,
    load_state,
    question_argv,
    save_state,
    search_argv,
)


def test_first_run_state_round_trip(tmp_path: Path) -> None:
    home, folder = tmp_path / "home", tmp_path / "documents"
    folder.mkdir()
    assert not load_state(home).onboarding_complete
    save_state(DesktopState(home, (folder,), ("What changed?",)))
    loaded = load_state(home)
    assert loaded.onboarding_complete
    assert loaded.folders == (folder.resolve(),)
    assert loaded.recent_questions == ("What changed?",)


def test_actions_delegate_to_bounded_cli(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    assert ingestion_argv(folder, tmp_path) == ["add", str(folder), "--home", str(tmp_path)]
    assert search_argv("  annual plan ", tmp_path)[0:2] == ["find", "annual plan"]
    assert question_argv("Why?", tmp_path)[0:2] == ["ask", "Why?"]
    with pytest.raises(ValueError, match="words"):
        search_argv(" ", tmp_path)


def test_persistent_document_and_failure_views_use_ledger(tmp_path: Path) -> None:
    layout = ArchivLayout.resolve(tmp_path)
    layout.ensure()
    database = ArchivDatabase(layout.database)
    database.initialize()
    with sqlite3.connect(layout.database) as connection:
        connection.execute(
            "INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?)",
            ("a" * 64, 1, "text/plain", ".txt", "original", "2026-01-01"),
        )
        for ident, status, error in (("ok", "succeeded", None), ("bad", "failed", "denied")):
            connection.execute(
                "INSERT INTO ingestions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, "a" * 64, f"/{ident}.txt", f"{ident}.txt", "2026-01-01", 0, status, error),
            )
        # The common failure case never reaches `ingestions` at all -- it was
        # rejected before storage (see ingestion/service.py::_record_ingestion_failure)
        # -- and it happened later than "bad.txt" above, so it must rank first.
        connection.execute(
            "INSERT INTO ingestion_failures VALUES (?, ?, ?, ?, ?, ?)",
            ("rejected", "/rejected.docx", "rejected.docx", None, "malformed", "2026-01-02"),
        )
        connection.commit()
    assert [row.name for row in list_documents(tmp_path)] == ["ok.txt"]
    failures = list_documents(tmp_path, failures=True)
    assert [row.name for row in failures] == ["rejected.docx", "bad.txt"]
    assert failures[0].error == "malformed"
    assert failures[1].error == "denied"


def test_search_and_questions_ask_for_readable_text(tmp_path: Path) -> None:
    # The window shows what the command prints; JSON is for programs, not for people.
    assert "--json" not in search_argv("annual plan", tmp_path)
    assert "--json" not in question_argv("Why?", tmp_path)


def test_an_archive_built_from_the_command_line_skips_first_run(tmp_path: Path) -> None:
    layout = ArchivLayout.resolve(tmp_path)
    layout.ensure()
    ArchivDatabase(layout.database).initialize()
    assert load_state(tmp_path).onboarding_complete


def _window(home: Path):  # type: ignore[no-untyped-def]
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    from archiv.ui.tk_product import ProductApp

    return root, ProductApp(root, home=home)


def test_library_is_one_scrollable_table_with_a_count(tmp_path: Path) -> None:
    from tkinter import ttk

    test_persistent_document_and_failure_views_use_ledger(tmp_path)
    root, app = _window(tmp_path)
    try:
        app._show("Needs attention")  # pyright: ignore[reportPrivateUsage]
        tables = [w for w in app.body.winfo_children() if isinstance(w, ttk.Treeview)]
        assert len(tables) == 1
        rows = [tables[0].item(i, "values") for i in tables[0].get_children()]
        assert [r[0] for r in rows] == ["rejected.docx", "bad.txt"]
        assert rows[0][2] == "malformed"
        labels = [w.cget("text") for w in app.body.winfo_children() if isinstance(w, ttk.Label)]
        assert "2 file(s)" in labels
    finally:
        root.destroy()


def test_empty_search_says_so_and_runs_nothing(tmp_path: Path) -> None:
    from tkinter import ttk

    root, app = _window(tmp_path)
    try:
        app._show("Search")  # pyright: ignore[reportPrivateUsage]
        button = next(
            w
            for w in app.body.winfo_children()
            if isinstance(w, ttk.Button) and w.cget("text") == "Search"
        )
        button.invoke()
        assert "Enter words" in app.status.get()
        assert not app.runner.busy
    finally:
        root.destroy()


def test_output_from_the_runner_thread_reaches_the_window(tmp_path: Path) -> None:
    import threading

    root, app = _window(tmp_path)
    try:
        app._show("Search")  # pyright: ignore[reportPrivateUsage]
        sender = threading.Thread(target=app._on_output, args=(b"Found 1 verified match(es)\n",))  # pyright: ignore[reportPrivateUsage]
        sender.start()
        sender.join()
        app._drain_output()  # pyright: ignore[reportPrivateUsage]
        root.update()
        assert "Found 1 verified match(es)" in app.output.get("1.0", "end")
    finally:
        root.destroy()
