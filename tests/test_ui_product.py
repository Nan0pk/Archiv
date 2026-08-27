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
        connection.commit()
    assert [row.name for row in list_documents(tmp_path)] == ["ok.txt"]
    failures = list_documents(tmp_path, failures=True)
    assert failures[0].name == "bad.txt"
    assert failures[0].error == "denied"
