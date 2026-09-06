"""Every ask that reached the model leaves a record saying so.

The gap these tests close was found by review, inside a sentence written to claim it
did not exist. The writes after the model call sat outside every guard, so a filesystem
failure escaped `run_grounded_ask` with the archive's text already sent to a remote
service, no result on disk, and no warning shown -- because the warning is driven by the
returned result. That is an unreported disclosure, which is the one thing the provenance
work exists to prevent.

Failures here are simulated, not caused: no disk is filled and no network is touched.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import RunStatus
from archiv.evaluation_config import mark_for_evaluation
from archiv.grounding import AskEvidenceUnwritableError, run_grounded_ask
from archiv.model_adapter import ModelConfig, save_model_config
from archiv.sample_vault import create_sample_vault

runner = CliRunner()

API_KEY_ENV = "ARCHIV_TEST_EVIDENCE_KEY"


class StubModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, prompt: str) -> str:
        del prompt
        self.calls += 1
        return self.reply

    def can_enforce_schema(self) -> bool:
        return False


def good_reply() -> str:
    return json.dumps(
        {
            "paragraphs": [{"paragraph_id": "P1", "text": "An answer.", "citation_ids": ["CIT-1"]}],
            "claims": [],
        }
    )


def remote_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home, corpus = tmp_path / "home", tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(
        ModelConfig(
            adapter="remote-evaluation",
            endpoint="https://api.openai.com",
            model="a-model",
            api_key_env=API_KEY_ENV,
        ),
        home,
    )
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    return home


def fail_writes_to(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    """Make one filename unwritable, the way a full disk would."""

    real_write_text = pathlib.Path.write_text

    def guarded(self: Path, *args: object, **kwargs: object) -> int:
        if self.name == filename or self.name == f"{filename}.tmp":
            raise OSError(28, "No space left on device")
        return real_write_text(self, *args, **kwargs)  # pyright: ignore[reportCallIssue,reportArgumentType]

    monkeypatch.setattr(pathlib.Path, "write_text", guarded)


def install_model(monkeypatch: pytest.MonkeyPatch, model: StubModel) -> None:
    def builder(config: object, home_arg: object = None) -> StubModel:
        del config, home_arg
        return model

    monkeypatch.setattr("archiv.grounding.build_model_adapter", builder)


def test_a_write_failure_after_the_model_call_still_records_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the transcript must not lose the record. The record is the evidence."""

    home = remote_archive(tmp_path, monkeypatch)
    model = StubModel(good_reply())
    install_model(monkeypatch, model)
    fail_writes_to(monkeypatch, "model_response.txt")

    result = run_grounded_ask("unique fixture marker", home=home)

    assert model.calls == 1, "the model ran, so the run must be recorded"
    recorded = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert recorded["model"]["provenance"] == "remote-evaluation"
    assert any("transcript could not be written" in error for error in result.errors)


def test_a_write_failure_after_the_model_call_still_warns_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When even the record cannot be written, the failure says the text was sent.

    This is the case with no result to drive the warning, so the warning comes from the
    error. Saying nothing here would be the worst outcome in the whole provenance story.
    """

    home = remote_archive(tmp_path, monkeypatch)
    model = StubModel(good_reply())
    install_model(monkeypatch, model)
    fail_writes_to(monkeypatch, "result.json")

    with pytest.raises(AskEvidenceUnwritableError) as caught:
        run_grounded_ask("unique fixture marker", home=home)

    assert model.calls == 1
    assert caught.value.model_was_called is True
    assert caught.value.model.provenance == "remote-evaluation"
    assert "record could not be written" in str(caught.value)


def test_paths_before_the_prompt_send_nothing_and_say_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure before the prompt is built is missing evidence, not a disclosure."""

    home = remote_archive(tmp_path, monkeypatch)
    model = StubModel(good_reply())
    install_model(monkeypatch, model)

    def exploding_retrieval(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("the search index is unreadable")

    monkeypatch.setattr("archiv.grounding.retrieve_evidence", exploding_retrieval)

    result = run_grounded_ask("unique fixture marker", home=home)

    assert model.calls == 0, "nothing may be sent when retrieval never produced evidence"
    assert result.status == RunStatus.FAILED
    assert any("retrieval failed" in error for error in result.errors)
    # Still recorded, and still stamped, even though no model ran.
    recorded = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert recorded["model"]["provenance"] == "remote-evaluation"

    # And an empty query is refused before a run directory is even created.
    with pytest.raises(ValueError, match="query cannot be empty"):
        run_grounded_ask("   ", home=home)


def test_no_ask_leaves_an_empty_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runs/` is terminal append-only evidence: a run that happened says it happened."""

    home = remote_archive(tmp_path, monkeypatch)
    install_model(monkeypatch, StubModel(good_reply()))

    # A run that answers, a run that finds nothing, and a run whose retrieval breaks.
    run_grounded_ask("unique fixture marker", home=home)
    run_grounded_ask("a phrase that appears in no document", home=home)

    def unreadable_index(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("unreadable index")

    monkeypatch.setattr("archiv.grounding.retrieve_evidence", unreadable_index)
    run_grounded_ask("unique fixture marker", home=home)

    run_dirs = sorted((home / "runs" / "ask").iterdir())
    assert len(run_dirs) == 3
    for run_dir in run_dirs:
        contents = sorted(path.name for path in run_dir.iterdir())
        assert contents, f"{run_dir.name} is an empty run directory"
        assert "result.json" in contents, (
            f"{run_dir.name} holds {contents} but no result.json, so nothing records "
            "how that run ended"
        )
