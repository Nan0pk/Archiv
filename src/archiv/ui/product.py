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

from pydantic import ValidationError

from archiv.model_adapter import (
    ModelConfig,
    build_model_adapter,
    load_model_config,
    save_model_config,
)
from archiv.storage.layout import ArchivLayout

PRODUCT_STATE = "desktop.json"
LOOPBACK_CANDIDATES = (
    ("http://127.0.0.1:11434", "Ollama"),
    ("http://127.0.0.1:1234", "LM Studio"),
    ("http://127.0.0.1:8080", "llama.cpp"),
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
        # An archive built from the command line has no folders saved by the app.
        return bool(self.folders) or ArchivLayout.resolve(self.home).database.is_file()


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


def detect_loopback_models(timeout: float = 0.15) -> tuple[tuple[str, str, str], ...]:
    """Every model offered by a known local server, as (endpoint, server, model).

    Only the fixed loopback addresses above are contacted, never a network address.
    """

    found: list[tuple[str, str, str]] = []
    for endpoint, label in LOOPBACK_CANDIDATES:
        try:
            with urlopen(endpoint + "/v1/models", timeout=timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            for item in payload.get("data", []):
                if isinstance(item.get("id"), str):
                    found.append((endpoint, label, str(item["id"])))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return tuple(found)


def configure_model(home: Path, endpoint: str, model: str) -> Path:
    """Save a local model choice; the loopback-only rules reject anything else."""

    try:
        config = ModelConfig(
            adapter="openai-compatible-loopback", endpoint=endpoint.strip(), model=model.strip()
        )
    except ValidationError as error:
        raise ValueError(error.errors()[0]["msg"].removeprefix("Value error, ")) from error
    return save_model_config(config, home)


def verify_model(home: Path) -> tuple[bool, str]:
    """Ask the saved model one tiny question, so "configured" is never only a claim."""

    config = load_model_config(home)
    try:
        reply = build_model_adapter(config, home).complete("Respond with the single word PONG.")
    except Exception as error:  # any failure is reported to the person, not raised
        return False, f"{config.model} at {config.endpoint} did not answer: {error}"
    return True, f"{config.model} at {config.endpoint} answered ({reply.strip()[:40]!r})."


def gguf_hint(path: Path) -> str:
    """Archiv never starts or downloads a model; say how to serve a model file locally."""

    return (
        f"Archiv talks to a model server on this computer rather than opening {path.name} "
        f"itself. Serve it with llama.cpp:\n  llama-server -m {path} --port 8080\n"
        "then choose it here (it appears as a llama.cpp model)."
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
    # The CLI's readable text, not JSON: it is what a person should read, and it already
    # prints the "not a local answer" banner above an answer when that applies.
    return ["find", query.strip(), "--home", str(home)]


def question_argv(question: str, home: Path) -> list[str]:
    if not question.strip():
        raise ValueError("Enter a question.")
    return ["ask", question.strip(), "--home", str(home)]
