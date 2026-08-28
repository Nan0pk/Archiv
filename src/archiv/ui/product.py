"""Headless application model for the Archiv desktop interface.

The model deliberately contains no ingestion, retrieval, source-location, or
report-validation implementation.  Actions are expressed as argv for the
existing bounded command layer; persistent UI-only preferences are kept next
to Archiv's configuration.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

from archiv.model_adapter import ModelConfig, save_model_config
from archiv.storage.layout import ArchivLayout

PRODUCT_STATE = "desktop.json"
LOOPBACK_CANDIDATES = (
    ("http://127.0.0.1:11434", "Ollama"),
    ("http://127.0.0.1:1234", "LM Studio"),
)


@dataclass(frozen=True, slots=True)
class Prerequisite:
    name: str
    available: bool
    guidance: str


@dataclass(frozen=True, slots=True)
class DocumentRow:
    name: str
    source_path: str
    status: str
    imported_at: str
    error: str | None


@dataclass(frozen=True, slots=True)
class DesktopState:
    home: Path
    folders: tuple[Path, ...]
    recent_questions: tuple[str, ...]

    @property
    def onboarding_complete(self) -> bool:
        return bool(self.folders)


def state_path(home: Path) -> Path:
    return ArchivLayout.resolve(home).config / PRODUCT_STATE


def load_state(home: Path | None = None) -> DesktopState:
    layout = ArchivLayout.resolve(home)
    path = state_path(layout.root)
    if not path.is_file():
        return DesktopState(layout.root, (), ())
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        folders = tuple(Path(item).expanduser().resolve() for item in value.get("folders", []))
        questions = tuple(str(item) for item in value.get("recent_questions", []))
    except (OSError, ValueError, TypeError):
        return DesktopState(layout.root, (), ())
    return DesktopState(layout.root, folders, questions[:20])


def save_state(state: DesktopState) -> None:
    layout = ArchivLayout.resolve(state.home)
    layout.ensure()
    target = state_path(layout.root)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "folders": [str(path) for path in state.folders],
                "recent_questions": list(state.recent_questions[:20]),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def check_prerequisites() -> tuple[Prerequisite, ...]:
    """Report optional converters without trying to reproduce their behavior."""

    ocr = shutil.which("tesseract") is not None
    office = shutil.which("libreoffice") is not None or shutil.which("soffice") is not None
    return (
        Prerequisite("OCR (Tesseract)", ocr, "Install tesseract for scanned documents."),
        Prerequisite("LibreOffice", office, "Install LibreOffice for legacy office files."),
    )


def detect_loopback_model(timeout: float = 0.15) -> tuple[str, str] | None:
    """Detect known local servers only; never contact a non-loopback address."""

    for endpoint, _label in LOOPBACK_CANDIDATES:
        try:
            with urlopen(endpoint + "/v1/models", timeout=timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            models = payload.get("data", [])
            if models and isinstance(models[0].get("id"), str):
                return endpoint, str(models[0]["id"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def configure_detected_model(home: Path, detected: tuple[str, str]) -> Path:
    endpoint, model = detected
    return save_model_config(
        ModelConfig(adapter="openai-compatible-loopback", endpoint=endpoint, model=model), home
    )


def list_documents(home: Path, *, failures: bool = False) -> tuple[DocumentRow, ...]:
    """Read the service-owned ledger without creating or interpreting records."""

    database = ArchivLayout.resolve(home).database
    if not database.is_file():
        return ()
    if not failures:
        query = """SELECT source_name, source_path, status, imported_at, error
                    FROM ingestions WHERE status = 'succeeded' ORDER BY imported_at DESC"""
    else:
        # Two distinct failure populations: a rare post-storage failure keeps its
        # ingestions row (status='failed'); a file rejected before storage -- the
        # common case -- has no ingestions row at all and lives only in
        # ingestion_failures (see ingestion/service.py::_record_ingestion_failure).
        # Both belong in this view, so union them.
        query = """
            SELECT source_name, source_path, status, imported_at, error
                FROM ingestions WHERE status = 'failed'
            UNION ALL
            SELECT source_name, source_path, 'failed', attempted_at, error
                FROM ingestion_failures
            ORDER BY imported_at DESC
        """
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(query).fetchall()  # noqa: S608
    except sqlite3.Error:
        return ()
    return tuple(DocumentRow(*row) for row in rows)


def ingestion_argv(folder: Path, home: Path) -> list[str]:
    return ["add", str(folder), "--home", str(home)]


def search_argv(query: str, home: Path) -> list[str]:
    if not query.strip():
        raise ValueError("Enter words to search for.")
    return ["find", query.strip(), "--home", str(home), "--json"]


def question_argv(question: str, home: Path) -> list[str]:
    if not question.strip():
        raise ValueError("Enter a question.")
    return ["ask", question.strip(), "--home", str(home), "--json"]
