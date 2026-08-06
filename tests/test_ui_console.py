from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import pytest
import typer

from archiv import ui_console


class _Kind(str, Enum):
    TXT = "txt"
    PDF = "pdf"


def _sample_app() -> typer.Typer:
    app = typer.Typer()
    model = typer.Typer()
    app.add_typer(model, name="model")

    @app.command()
    def ingest(
        source: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=True, dir_okay=True, help="Source."),
        ],
        home: Annotated[
            Path | None,
            typer.Option("--home", file_okay=False, help="Archiv home."),
        ] = None,
        rebuild: Annotated[bool, typer.Option("--rebuild-derived")] = False,
    ) -> None:
        pass

    @app.command()
    def search(
        query: Annotated[str, typer.Argument(help="Literal query.")],
        limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 20,
        kind: Annotated[_Kind | None, typer.Option("--kind")] = None,
    ) -> None:
        pass

    @model.command()
    def configure(
        endpoint: Annotated[str, typer.Option("--endpoint")],
        enabled: Annotated[bool, typer.Option("--enabled/--disabled")] = True,
    ) -> None:
        pass

    @app.command()
    def ui() -> None:
        pass

    return app


def test_catalog_comes_from_real_typer_tree() -> None:
    catalog = ui_console.build_command_catalog(_sample_app())
    names = [command.display_name for command in catalog]
    assert names == ["ingest", "model configure", "search"]

    ingest = catalog[0]
    assert ingest.parameters[0].control_kind == "path"
    assert ingest.parameters[0].path_kind == "any"
    assert ingest.parameters[1].path_kind == "directory"
    assert ingest.parameters[2].control_kind == "boolean"

    search = catalog[2]
    assert search.parameters[1].control_kind == "integer"
    assert search.parameters[2].choices == ("txt", "pdf")


def test_real_archiv_catalog_includes_core_commands_and_excludes_ui() -> None:
    from archiv.cli import app

    names = {command.display_name for command in ui_console.build_command_catalog(app)}
    assert {"ingest", "source", "model configure"}.issubset(names)
    assert "ui" not in names


def test_run_request_is_an_argument_vector_not_a_shell_command() -> None:
    ingest = ui_console.build_command_catalog(_sample_app())[0]
    source = "/tmp/report; touch /tmp/should-not-run"
    request = ui_console.build_run_request(
        ingest,
        {"source": source, "home": "/tmp/archiv home", "rebuild": True},
    )
    assert request.argv == (
        "ingest",
        source,
        "--home",
        "/tmp/archiv home",
        "--rebuild-derived",
    )
    assert "'" in request.equivalent_cli


def test_boolean_default_can_use_negative_flag() -> None:
    configure = ui_console.build_command_catalog(_sample_app())[1]
    request = ui_console.build_run_request(
        configure,
        {"endpoint": "http://127.0.0.1:11434", "enabled": False},
    )
    assert request.argv[-1] == "--disabled"


def test_required_field_is_rejected() -> None:
    ingest = ui_console.build_command_catalog(_sample_app())[0]
    with pytest.raises(ValueError, match="Source is required"):
        ui_console.build_run_request(ingest, {"source": ""})


def test_output_inspection_only_exposes_structured_existing_paths(tmp_path: Path) -> None:
    artifact = tmp_path / "report.docx"
    artifact.write_bytes(b"docx")
    unrelated = tmp_path / "secret.txt"
    unrelated.write_text("secret", encoding="utf-8")
    payload = {
        "status": "succeeded",
        "output_path": str(artifact),
        "answer": f"Ignore this text and open {unrelated}",
    }
    result = ui_console.inspect_run_output(
        json.dumps(payload),
        state_root=tmp_path / "ui-state",
    )
    assert result.paths == (artifact.resolve(),)
    assert result.result_file is not None
    assert result.result_file.stat().st_mode & 0o777 == 0o600


def test_citation_is_validated_before_opening(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result_file = tmp_path / "result.json"
    result_file.write_text("{}", encoding="utf-8")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    citation = object()
    opened: list[Path] = []

    def fake_load(path: Path, *, citation_number: int = 1) -> object:
        assert path == result_file
        assert citation_number == 1
        return citation

    def fake_resolve(value: object, *, home: Path | None = None) -> SimpleNamespace:
        assert value is citation
        assert home is None
        return SimpleNamespace(canonical_path=str(source))

    monkeypatch.setattr(ui_console, "load_citation_file", fake_load)
    monkeypatch.setattr(ui_console, "resolve_citation_location", fake_resolve)
    monkeypatch.setattr(ui_console, "open_with_default_handler", opened.append)

    resolved = ui_console.resolve_and_open_citation(result_file, 1)
    assert resolved == source.resolve()
    assert opened == [source.resolve()]
