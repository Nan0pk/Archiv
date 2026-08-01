"""Human-facing commands built on Archiv's verified core primitives."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, cast
from uuid import uuid4

import typer
from pydantic import BaseModel

from archiv.contracts import IngestionResult, RunStatus, SearchIndexBuild
from archiv.ingestion import ingest_file
from archiv.ingestion.formats import SUPPORTED_SUFFIXES
from archiv.model_adapter import load_model_config
from archiv.search import rebuild_search_index, search_documents
from archiv.search.index import search_index_path
from archiv.search.schema import connect_index
from archiv.storage.layout import ArchivLayout
from archiv.task_contracts import TaskRunResult
from archiv.tasks import run_task, verify_task_run


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _emit_json(value: object) -> None:
    typer.echo(json.dumps(_json_value(value), indent=2, sort_keys=True))


def _candidates(source: Path) -> tuple[list[Path], int]:
    if source.is_file():
        return [source], 0
    files = sorted(path for path in source.rglob("*") if path.is_file())
    supported = [path for path in files if path.suffix.lower() in SUPPORTED_SUFFIXES]
    return supported, len(files) - len(supported)


def _add_sources(
    source: Path,
    *,
    home: Path | None,
    rebuild_derived: bool,
) -> tuple[list[IngestionResult], SearchIndexBuild, int]:
    candidates, skipped = _candidates(source)
    if not candidates:
        raise ValueError("no supported files were found")

    results: list[IngestionResult] = []
    active = source
    try:
        for active in candidates:
            results.append(ingest_file(active, home=home, rebuild_derived=rebuild_derived))
    except (OSError, RuntimeError, ValueError) as error:
        if results:
            rebuild_search_index(home=home)
        raise RuntimeError(f"{active}: {type(error).__name__}: {error}") from error
    return results, rebuild_search_index(home=home), skipped


def _locator_text(locator: dict[str, object]) -> str:
    return ", ".join(f"{key} {value}" for key, value in sorted(locator.items())) or "source"


def _excerpt(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _status_payload(home: Path | None) -> dict[str, object]:
    layout = ArchivLayout.resolve(home)
    counts = {
        "objects": 0,
        "successful_ingestions": 0,
        "duplicate_ingestions": 0,
        "failed_ingestions": 0,
    }
    errors: list[str] = []
    if layout.database.is_file():
        try:
            with sqlite3.connect(layout.database) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM objects) AS objects,
                        (SELECT COUNT(*) FROM ingestions WHERE status = 'succeeded')
                            AS successful_ingestions,
                        (SELECT COUNT(*) FROM ingestions
                            WHERE status = 'succeeded' AND duplicate = 1)
                            AS duplicate_ingestions,
                        (SELECT COUNT(*) FROM ingestions WHERE status = 'failed')
                            AS failed_ingestions
                    """
                ).fetchone()
            if row is not None:
                for key in counts:
                    counts[key] = int(row[key])
        except sqlite3.Error as error:
            errors.append(f"metadata database: {error}")

    index_objects = 0
    index_segments = 0
    index_path = search_index_path(layout)
    if index_path.is_file():
        try:
            with connect_index(index_path) as connection:
                metadata = {
                    str(row["key"]): str(row["value"])
                    for row in connection.execute(
                        "SELECT key, value FROM index_metadata"
                    ).fetchall()
                }
            index_objects = int(metadata.get("object_count", "0"))
            index_segments = int(metadata.get("segment_count", "0"))
        except (sqlite3.Error, ValueError) as error:
            errors.append(f"search index: {error}")

    run_counts: dict[str, int] = {}
    task_root = layout.runs / "tasks"
    if task_root.is_dir():
        for result_path in sorted(task_root.glob("*/result.json")):
            try:
                result = TaskRunResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                errors.append(f"task result {result_path.parent.name}: {error}")
                continue
            status = str(result.status)
            run_counts[status] = run_counts.get(status, 0) + 1

    model = load_model_config(layout.root)
    return {
        "schema_version": "1",
        "home": str(layout.root),
        "documents": counts["objects"],
        "ingestions": {
            "successful": counts["successful_ingestions"],
            "duplicates": counts["duplicate_ingestions"],
            "failed": counts["failed_ingestions"],
        },
        "search_index": {
            "available": index_path.is_file(),
            "documents": index_objects,
            "passages": index_segments,
        },
        "reports": run_counts,
        "model": model.model_dump(mode="json"),
        "errors": errors,
    }


def register_user_commands(app: typer.Typer) -> tuple[Callable[..., None], ...]:
    """Attach the small everyday command surface to the root CLI."""

    @app.command("add")
    def add_command(
        source: Annotated[
            Path,
            typer.Argument(
                exists=True,
                file_okay=True,
                dir_okay=True,
                readable=True,
                resolve_path=True,
                help="File or folder to preserve and make searchable.",
            ),
        ],
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, resolve_path=True),
        ] = None,
        rebuild_derived: Annotated[
            bool,
            typer.Option(
                "--rebuild-derived",
                help="Rebuild normalized data even when the content already exists.",
            ),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Add supported files and immediately refresh the local search index."""

        try:
            results, index, skipped = _add_sources(
                source,
                home=home,
                rebuild_derived=rebuild_derived,
            )
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"add failed: {error}", err=True)
            raise typer.Exit(code=1) from error

        payload = {
            "schema_version": "1",
            "status": "succeeded",
            "source": str(source),
            "added": [result.model_dump(mode="json") for result in results],
            "new_originals": sum(not result.duplicate for result in results),
            "duplicates": sum(result.duplicate for result in results),
            "skipped_unsupported": skipped,
            "search_index": index.model_dump(mode="json"),
        }
        if json_output:
            _emit_json(payload)
            return

        typer.echo(
            f"Added: {len(results)} file(s) "
            f"({payload['new_originals']} new, {payload['duplicates']} duplicate)"
        )
        if skipped:
            typer.echo(f"Skipped unsupported: {skipped}")
        typer.echo(f"Indexed: {index.object_count} document(s), {index.segment_count} passage(s)")
        typer.echo(f"Archiv home: {ArchivLayout.resolve(home).root}")

    @app.command("find")
    def find_command(
        query: Annotated[str, typer.Argument(help="Exact words or phrase to find.")],
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, resolve_path=True),
        ] = None,
        limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Find exact text and show readable, independently validated citations."""

        try:
            results = search_documents(query, home=home, limit=limit)
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"find failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        if json_output:
            _emit_json([result.model_dump(mode="json") for result in results])
            return
        if not results:
            typer.echo(f"No verified matches found for: {query}")
            return

        typer.echo(f"Found {len(results)} verified match(es)")
        for number, result in enumerate(results, start=1):
            citation = result.citation
            typer.echo(f"{number}. {citation.source_name} — {_locator_text(citation.locator)}")
            typer.echo(f"   {_excerpt(result.text)}")

    @app.command("report")
    def report_command(
        query: Annotated[str, typer.Argument(help="Question or exact evidence phrase.")],
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, resolve_path=True),
        ] = None,
        title: Annotated[str, typer.Option("--title")] = "Archiv Evidence Report",
        max_sources: Annotated[int, typer.Option("--max-sources", min=1, max=50)] = 8,
        render: Annotated[bool, typer.Option("--render/--no-render")] = True,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Create and independently verify a cited DOCX without a task file."""

        layout = ArchivLayout.resolve(home)
        layout.ensure()
        task_path = layout.temporary / f"user-report-{uuid4().hex}.json"
        task_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "task": "cross-file-report",
                    "query": query,
                    "title": title,
                    "output_name": "archiv-report.docx",
                    "max_sources": max_sources,
                    "render": render,
                    "model_policy": "disabled",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            result = run_task(task_path, home=layout.root)
            if result.status is not RunStatus.SUCCEEDED:
                detail = "; ".join(result.errors) or str(result.status)
                raise RuntimeError(detail)
            verification = verify_task_run(result.run_id, home=layout.root, render=render)
            if not verification.valid:
                raise RuntimeError("; ".join(verification.errors) or "verification failed")
            if result.output_path is None:
                raise RuntimeError("successful report run did not record an output path")
        except (OSError, RuntimeError, ValueError) as error:
            typer.echo(f"report failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        finally:
            task_path.unlink(missing_ok=True)

        payload = {
            "schema_version": "1",
            "status": "succeeded",
            "run": result.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
        }
        if json_output:
            _emit_json(payload)
            return
        typer.echo(f"Report: {result.output_path}")
        typer.echo(f"Citations: {result.citation_count}")
        typer.echo("Verified: yes")
        typer.echo(f"Run: {result.run_id}")

    @app.command("status")
    def status_command(
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, resolve_path=True),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Show the useful state of the local archive without changing it."""

        payload = _status_payload(home)
        if json_output:
            _emit_json(payload)
            return

        ingestions = cast(dict[str, object], payload["ingestions"])
        index = cast(dict[str, object], payload["search_index"])
        reports = cast(dict[str, int], payload["reports"])
        model = cast(dict[str, object], payload["model"])
        typer.echo(f"Archiv home: {payload['home']}")
        typer.echo(f"Documents: {payload['documents']}")
        typer.echo(
            "Ingestions: "
            f"{ingestions['successful']} succeeded, "
            f"{ingestions['duplicates']} duplicate, "
            f"{ingestions['failed']} failed"
        )
        if index["available"]:
            typer.echo(
                f"Search index: {index['documents']} document(s), {index['passages']} passage(s)"
            )
        else:
            typer.echo("Search index: not built")
        typer.echo(
            f"Reports: {reports.get('succeeded', 0)} succeeded, "
            f"{sum(reports.values()) - reports.get('succeeded', 0)} other"
        )
        typer.echo(f"Model: {model['adapter']}")
        for error in cast(list[str], payload["errors"]):
            typer.echo(f"Warning: {error}", err=True)

    return add_command, find_command, report_command, status_command
