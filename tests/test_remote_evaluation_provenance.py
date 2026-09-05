"""An answer from somebody else's computer must never look like a local one.

Archiv's whole claim is that documents stay on your machine. Evaluation mode is the one
exception, so the moment it is in use every surface that shows an answer has to say so.
The dangerous failure is not a crash -- it is an answer that looks exactly like a local
one and is quietly believed.

The third test here is the important one. It walks every way `ask` can finish, including
the two that never call a model at all, and insists each one carries the stamp. A stamp
that is present on the happy path and missing on a failure is worse than none, because it
teaches a reader to trust its absence.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import RunStatus
from archiv.evaluation_config import mark_for_evaluation
from archiv.grounding import run_grounded_ask
from archiv.model_adapter import ModelConfig, save_model_config
from archiv.sample_vault import create_sample_vault
from archiv.ui.outputs import inspect_run_output

runner = CliRunner()

API_KEY_ENV = "ARCHIV_TEST_PROVENANCE_KEY"


def remote_config() -> ModelConfig:
    return ModelConfig(
        adapter="remote-evaluation",
        endpoint="https://api.openai.com",
        model="a-model-name",
        api_key_env=API_KEY_ENV,
    )


def good_reply() -> str:
    return json.dumps(
        {
            "paragraphs": [{"paragraph_id": "P1", "text": "An answer.", "citation_ids": ["CIT-1"]}],
            "claims": [],
        }
    )


class StubModel:
    def __init__(self, reply: str = "") -> None:
        self.reply = reply

    def complete(self, prompt: str) -> str:
        del prompt
        return self.reply

    def can_enforce_schema(self) -> bool:
        return False


def stub_builder(reply: str) -> Callable[..., StubModel]:
    """A stand-in for `build_model_adapter` that returns a scripted model."""

    def build(config: object, home_arg: object = None) -> StubModel:
        del config, home_arg
        return StubModel(reply)

    return build


def prepare_remote_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An archive configured for a remote model and marked for evaluation."""

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(remote_config(), home)
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    return home


def test_ask_result_json_records_remote_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = prepare_remote_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "archiv.grounding.build_model_adapter",
        stub_builder(good_reply()),
    )

    result = run_grounded_ask("unique fixture marker", home=home)
    assert result.model.provenance == "remote-evaluation"

    # The stamp has to survive into the file on disk, not just the object in memory.
    recorded = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert recorded["model"]["provenance"] == "remote-evaluation"
    request = json.loads((Path(result.evidence_dir) / "request.json").read_text())
    assert request["model"]["provenance"] == "remote-evaluation"

    machine = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(home), "--json"])
    assert json.loads(machine.output)["model"]["provenance"] == "remote-evaluation"


def test_ask_human_output_states_the_answer_is_not_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = prepare_remote_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "archiv.grounding.build_model_adapter",
        stub_builder(good_reply()),
    )

    shown = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(home)])
    assert shown.exit_code == 0
    output = shown.output

    assert "NOT A LOCAL ANSWER" in output
    assert "computers you do not control" in output
    assert "api.openai.com" in output
    assert "archiv model evaluation disable" in output

    # Above the answer, not below it. Below is too late to be a warning: by then the
    # reader has already read and believed it.
    assert output.index("NOT A LOCAL ANSWER") < output.index("Question:")
    assert output.index("NOT A LOCAL ANSWER") < output.index("Verified Sources:")

    assert "Answered by: a model running on computers you do not control" in output


def test_a_local_answer_is_not_stamped_and_says_it_is_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The banner is only for remote runs. On every run it would be trained away."""

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(
        ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint="http://127.0.0.1:11434",
            model="a-local-model",
        ),
        home,
    )
    monkeypatch.setattr(
        "archiv.grounding.build_model_adapter",
        stub_builder(good_reply()),
    )

    shown = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(home)])
    assert "NOT A LOCAL ANSWER" not in shown.output
    assert "Answered by: a model running on this machine" in shown.output


def test_no_remote_ask_can_produce_an_unstamped_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every way `ask` can finish carries the stamp, including the ways that never
    call a model at all.

    The list of endings is read out of the source rather than written down here, so
    adding an eighth ending without a stamp fails this test instead of silently
    creating an unstamped surface.
    """

    import archiv.grounding as grounding_module

    tree = ast.parse(Path(grounding_module.__file__).read_text())
    endings = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AskRunResult"
    ]
    assert len(endings) >= 6, "expected every terminal branch to build an AskRunResult"
    for call in endings:
        supplied = {keyword.arg for keyword in call.keywords}
        assert "model" in supplied, (
            f"the AskRunResult at grounding.py:{call.lineno} does not carry the model "
            "config, so its provenance would be missing from the run evidence"
        )

    # Now exercise the endings that can be reached without a live model, and confirm
    # each one really does write the stamp to disk.
    home = prepare_remote_archive(tmp_path, monkeypatch)

    # 1. Refused because the archive is not marked for evaluation.
    unmarked = tmp_path / "unmarked"
    create_sample_vault(tmp_path / "corpus2")
    runner.invoke(app, ["add", str(tmp_path / "corpus2"), "--home", str(unmarked)])
    save_model_config(remote_config(), unmarked)
    refused = run_grounded_ask("unique fixture marker", home=unmarked)
    assert refused.status == RunStatus.BLOCKED_BY_POLICY
    assert refused.model.provenance == "remote-evaluation"
    assert (
        json.loads((Path(refused.evidence_dir) / "result.json").read_text())["model"]["provenance"]
        == "remote-evaluation"
    )
    # The refusal prints only its error text, so that text has to explain itself.
    shown = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(unmarked)])
    assert shown.exit_code == 1
    assert "not marked for evaluation" in shown.output
    assert "outside this machine" in shown.output

    # 2. Succeeded with no evidence found, which never calls a model.
    no_evidence = run_grounded_ask("a phrase that appears in no document", home=home)
    assert no_evidence.status == RunStatus.SUCCEEDED
    assert no_evidence.model.provenance == "remote-evaluation"
    assert (
        json.loads((Path(no_evidence.evidence_dir) / "result.json").read_text())["model"][
            "provenance"
        ]
        == "remote-evaluation"
    )

    # 3. The model replied with something unusable.
    monkeypatch.setattr(
        "archiv.grounding.build_model_adapter",
        stub_builder("not json at all"),
    )
    invalid = run_grounded_ask("unique fixture marker", home=home)
    assert invalid.status == RunStatus.PARTIALLY_PRODUCED_BUT_INVALID
    assert invalid.model.provenance == "remote-evaluation"

    # 4. The model call itself failed.
    def exploding(config: object, home_arg: object = None) -> StubModel:
        del config, home_arg
        raise RuntimeError("the model could not be reached")

    monkeypatch.setattr("archiv.grounding.build_model_adapter", exploding)
    failed = run_grounded_ask("unique fixture marker", home=home)
    assert failed.status == RunStatus.FAILED
    assert failed.model.provenance == "remote-evaluation"


def test_the_desktop_console_carries_the_stamp_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop console reads the run's JSON, so it must read the origin out of it."""

    home = prepare_remote_archive(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "archiv.grounding.build_model_adapter",
        stub_builder(good_reply()),
    )

    machine = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(home), "--json"])
    outputs = inspect_run_output(machine.output, home=home)
    assert outputs.model_provenance == "remote-evaluation"

    # A run that says nothing about a model leaves it unset rather than guessing local.
    assert inspect_run_output("{}").model_provenance is None
    assert inspect_run_output("not json").model_provenance is None
    assert inspect_run_output(json.dumps({"model": "a string"})).model_provenance is None
