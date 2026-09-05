"""The mark that lets one archive be used with a model running outside this machine.

Every test here is about refusing. The feature's whole job is to stay off unless
somebody deliberately turned it on and left a record of doing so, and the ways it
could fail open -- a missing file, a damaged file, a hand-written `true` with no
record behind it -- matter more than the way it succeeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.evaluation_config import (
    ACKNOWLEDGEMENT,
    ENABLE_COMMAND,
    ENV_OVERRIDE,
    EvaluationConfig,
    EvaluationNotEnabledError,
    check_evaluation_opt_in,
    clear_evaluation_mark,
    evaluation_config_path,
    load_evaluation_config,
    mark_for_evaluation,
    save_evaluation_config,
)
from archiv.storage.layout import ArchivLayout

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a stray variable in the developer's shell from turning this on mid-test."""

    monkeypatch.delenv(ENV_OVERRIDE, raising=False)


def _write_raw(home: Path, text: str) -> Path:
    path = evaluation_config_path(ArchivLayout.resolve(home))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_marker_means_not_an_evaluation_archive(tmp_path: Path) -> None:
    home = tmp_path / "home"

    config = load_evaluation_config(home)
    assert config.evaluation is False
    assert config.acknowledged_at is None
    assert config.acknowledgement is None
    assert not evaluation_config_path(ArchivLayout.resolve(home)).exists()

    with pytest.raises(EvaluationNotEnabledError):
        check_evaluation_opt_in(home)


def test_check_raises_with_remediation_naming_the_exact_command(tmp_path: Path) -> None:
    with pytest.raises(EvaluationNotEnabledError) as caught:
        check_evaluation_opt_in(tmp_path / "home")

    message = str(caught.value)
    # The command has to be the real one, not prose describing it, so a reader can paste it.
    assert ENABLE_COMMAND in message
    assert ENV_OVERRIDE in message
    # And the message has to say what actually happens, not just that something is off.
    assert "leave this machine" in message or "outside this machine" in message
    assert "private or confidential" in message


def test_corrupt_marker_file_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"

    for body in (
        "",
        "not json at all",
        "{",
        "[]",
        "null",
        json.dumps({"evaluation": "yes"}),
        json.dumps({"evaluation": True, "unexpected_key": 1}),
    ):
        _write_raw(home, body)
        assert load_evaluation_config(home).evaluation is False, body
        with pytest.raises(EvaluationNotEnabledError):
            check_evaluation_opt_in(home)


def test_a_bare_true_without_a_record_is_not_consent(tmp_path: Path) -> None:
    """Hand-editing the file to `true` must not switch the archive on."""

    home = tmp_path / "home"

    for payload in (
        {"evaluation": True},
        {"evaluation": True, "acknowledged_at": "2026-01-01T00:00:00+00:00"},
        {"evaluation": True, "acknowledgement": ACKNOWLEDGEMENT},
    ):
        _write_raw(home, json.dumps(payload))
        assert load_evaluation_config(home).evaluation is False, payload
        with pytest.raises(EvaluationNotEnabledError):
            check_evaluation_opt_in(home)

    with pytest.raises(ValueError, match="must record when the acknowledgement was given"):
        EvaluationConfig(evaluation=True)


def test_marker_records_an_acknowledgement_timestamp(tmp_path: Path) -> None:
    home = tmp_path / "home"

    config = mark_for_evaluation(home)
    assert config.evaluation is True
    assert config.acknowledgement == ACKNOWLEDGEMENT
    assert config.acknowledged_at is not None
    # A timestamp that cannot be compared to anything is not an audit record.
    assert config.acknowledged_at.endswith("+00:00")

    reloaded = load_evaluation_config(home)
    assert reloaded == config
    assert check_evaluation_opt_in(home) == config

    stored = json.loads(evaluation_config_path(ArchivLayout.resolve(home)).read_text())
    assert stored["acknowledged_at"] == config.acknowledged_at
    assert stored["acknowledgement"] == ACKNOWLEDGEMENT


def test_clearing_the_mark_refuses_again(tmp_path: Path) -> None:
    home = tmp_path / "home"
    mark_for_evaluation(home)
    assert check_evaluation_opt_in(home).evaluation is True

    clear_evaluation_mark(home)
    assert load_evaluation_config(home).evaluation is False
    with pytest.raises(EvaluationNotEnabledError):
        check_evaluation_opt_in(home)


def test_environment_override_records_that_it_was_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override exists for tests, and must not claim a person agreed to anything."""

    home = tmp_path / "home"
    monkeypatch.setenv(ENV_OVERRIDE, "1")

    config = check_evaluation_opt_in(home)
    assert config.evaluation is True
    assert config.acknowledgement is not None
    assert ENV_OVERRIDE in config.acknowledgement
    assert config.acknowledgement != ACKNOWLEDGEMENT

    # It is a per-process override, not a change to the archive.
    assert not evaluation_config_path(ArchivLayout.resolve(home)).exists()

    for value in ("0", "no", "off", ""):
        monkeypatch.setenv(ENV_OVERRIDE, value)
        assert load_evaluation_config(home).evaluation is False


def test_saving_replaces_the_file_without_leaving_a_temporary_behind(tmp_path: Path) -> None:
    home = tmp_path / "home"
    save_evaluation_config(EvaluationConfig(), home)
    mark_for_evaluation(home)

    config_dir = ArchivLayout.resolve(home).config
    assert [p.name for p in sorted(config_dir.iterdir())] == ["evaluation.json"]


def test_cli_refuses_to_enable_without_the_acknowledgement_flag(tmp_path: Path) -> None:
    home = tmp_path / "home"

    refused = runner.invoke(app, ["model", "evaluation", "enable", "--home", str(home)])
    assert refused.exit_code == 1
    assert load_evaluation_config(home).evaluation is False

    accepted = runner.invoke(
        app,
        [
            "model",
            "evaluation",
            "enable",
            "--acknowledge-documents-leave-this-machine",
            "--home",
            str(home),
        ],
    )
    assert accepted.exit_code == 0
    assert load_evaluation_config(home).evaluation is True

    status = runner.invoke(app, ["model", "evaluation", "status", "--home", str(home), "--json"])
    assert status.exit_code == 0
    assert json.loads(status.output)["acknowledgement"] == ACKNOWLEDGEMENT

    off = runner.invoke(app, ["model", "evaluation", "disable", "--home", str(home)])
    assert off.exit_code == 0
    assert load_evaluation_config(home).evaluation is False


def test_the_mark_travels_inside_a_backup(tmp_path: Path) -> None:
    """Documented behaviour, pinned: restoring an evaluation archive keeps it marked.

    `config/` is one of the durable directories, so the mark is carried in backups and
    exports. That is deliberate -- a restored copy that had quietly forgotten it was
    allowed to send documents out would be worse -- but it means the permission moves
    with the data, so it is worth a test rather than only a sentence in the README.
    """

    from archiv.archive import create_archive, restore_archive

    source, restored = tmp_path / "source", tmp_path / "restored"
    original = mark_for_evaluation(source)

    bundle = tmp_path / "backup.zip"
    create_archive(bundle, home=source)
    restore_archive(bundle, home=restored)

    carried = load_evaluation_config(restored)
    assert carried.evaluation is True
    assert carried.acknowledged_at == original.acknowledged_at
    assert carried.acknowledgement == ACKNOWLEDGEMENT
    assert check_evaluation_opt_in(restored).evaluation is True
