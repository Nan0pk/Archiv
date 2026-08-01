from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from archiv.archive import ARCHIVE_MANIFEST
from archiv.cli import app
from archiv.model_adapter import ModelConfig, load_model_config, save_model_config
from archiv.sample_vault import SAMPLE_FILES, create_sample_vault

runner = CliRunner()


def _task(path: Path, *, render: bool = False) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "task": "cross-file-report",
                "query": "unique fixture marker",
                "title": "Offline alpha test report",
                "output_name": "test-report.docx",
                "max_sources": 8,
                "render": render,
                "model_policy": "disabled",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_directory_ingest_run_verify_backup_restore(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    home = tmp_path / "home"

    ingested = runner.invoke(app, ["ingest", str(corpus), "--home", str(home)])
    assert ingested.exit_code == 0, ingested.output
    ingestion = json.loads(ingested.output)
    assert ingestion["status"] == "succeeded"
    assert len(ingestion["ingested"]) == len(SAMPLE_FILES)

    searched = runner.invoke(app, ["search", "unique fixture marker", "--home", str(home)])
    assert searched.exit_code == 0, searched.output
    assert len(json.loads(searched.output)) == len(SAMPLE_FILES)

    task_path = _task(tmp_path / "task.yaml")
    completed = runner.invoke(app, ["run", str(task_path), "--home", str(home)])
    assert completed.exit_code == 0, completed.output
    run = json.loads(completed.output)
    assert run["status"] == "succeeded"
    assert run["citation_count"] == len(SAMPLE_FILES)

    verified = runner.invoke(app, ["verify", run["run_id"], "--home", str(home)])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True

    backup_path = tmp_path / "archiv-backup.zip"
    backed_up = runner.invoke(app, ["backup", str(backup_path), "--home", str(home), "--json"])
    assert backed_up.exit_code == 0, backed_up.output
    with ZipFile(backup_path) as archive:
        names = set(archive.namelist())
        assert ARCHIVE_MANIFEST in names
        assert not any(name.startswith("indexes/") for name in names)
        assert not any(name.startswith("temporary/") for name in names)

    restored_home = tmp_path / "restored"
    restored = runner.invoke(
        app, ["restore", str(backup_path), "--home", str(restored_home), "--json"]
    )
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.output)["search_index_rebuilt"] is True

    restored_search = runner.invoke(
        app,
        ["search", "unique fixture marker", "--home", str(restored_home)],
    )
    assert restored_search.exit_code == 0, restored_search.output
    assert len(json.loads(restored_search.output)) == len(SAMPLE_FILES)

    restored_verify = runner.invoke(
        app,
        ["verify", run["run_id"], "--home", str(restored_home), "--no-render"],
    )
    assert restored_verify.exit_code == 0, restored_verify.output
    assert json.loads(restored_verify.output)["valid"] is True


def test_model_configuration_is_explicit_and_loopback_only(tmp_path: Path) -> None:
    assert load_model_config(tmp_path).adapter == "disabled"
    configured = ModelConfig(
        adapter="openai-compatible-loopback",
        endpoint="http://127.0.0.1:11434",
        model="local-test-model",
    )
    save_model_config(configured, tmp_path)
    assert load_model_config(tmp_path) == configured

    with pytest.raises(ValueError, match="loopback"):
        ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint="https://api.example.com",
            model="remote-model",
        )


def test_sample_vault_refuses_implicit_replacement(tmp_path: Path) -> None:
    destination = create_sample_vault(tmp_path / "sample")
    with pytest.raises(FileExistsError):
        create_sample_vault(destination)
