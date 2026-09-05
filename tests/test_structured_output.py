"""Schema enforcement as a declared capability, and schema failure as a counted outcome.

The failure being prevented is a quiet one. Swapping the model behind Archiv can stop
schema enforcement happening at all while every visible signal still says success, so it
gets misdiagnosed as the new model simply being worse. These tests hold three lines:

- a backend has to *say* whether it can enforce a schema, and anything that has not said
  yes counts as no;
- asking for enforcement that is not available fails loudly rather than proceeding
  unenforced;
- an unusable reply is retried a bounded number of times with the actual complaint fed
  back, and the two kinds of unusable reply are counted apart.

No test makes a network call; every model here is a stand-in that returns a canned reply.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archiv.grounding import classify_grounded_response
from archiv.grounding_contracts import GroundedModelResponse
from archiv.model_adapter import (
    DisabledModelAdapter,
    ModelConfig,
    OpenAICompatibleLoopbackAdapter,
    RemoteEvaluationAdapter,
)
from archiv.structured_output import (
    DEFAULT_MAX_ATTEMPTS,
    SchemaEnforcementUnavailableError,
    adapter_can_enforce_schema,
    count_unsupported_statements,
    request_grounded_response,
)

ALLOWED = {"CIT-1", "CIT-2"}


def good_reply(citation: str = "CIT-1") -> str:
    return json.dumps(
        {
            "paragraphs": [
                {"paragraph_id": "P1", "text": "An answer.", "citation_ids": [citation]}
            ],
            "claims": [],
        }
    )


def cites_something_it_was_never_given() -> str:
    return json.dumps(
        {
            "paragraphs": [
                {"paragraph_id": "P1", "text": "An answer.", "citation_ids": ["CIT-99"]}
            ],
            "claims": [],
        }
    )


class ScriptedModel:
    """Returns canned replies in order, and records every prompt it was given."""

    def __init__(self, *replies: str, enforces: bool | None = None) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self._enforces = enforces

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("the model was called more times than the test scripted")
        return self.replies.pop(0)

    def can_enforce_schema(self) -> bool:
        return bool(self._enforces)


class UndeclaredModel:
    """A backend that never says anything about schemas -- the pessimistic default case."""

    def complete(self, prompt: str) -> str:
        del prompt
        return good_reply()


def test_backend_declares_whether_it_can_enforce_a_schema() -> None:
    # Every adapter Archiv ships says no, and each says it deliberately: none of them
    # sends a schema constraint, and none has been measured enforcing one.
    assert adapter_can_enforce_schema(DisabledModelAdapter()) is False
    assert (
        adapter_can_enforce_schema(
            OpenAICompatibleLoopbackAdapter(
                ModelConfig(
                    adapter="openai-compatible-loopback",
                    endpoint="http://127.0.0.1:11434",
                    model="a-model",
                )
            )
        )
        is False
    )
    assert (
        adapter_can_enforce_schema(
            RemoteEvaluationAdapter(
                ModelConfig(
                    adapter="remote-evaluation",
                    endpoint="https://api.openai.com",
                    model="a-model",
                    api_key_env="A_KEY",
                )
            )
        )
        is False
    )

    # Anything that has not declared inherits no, rather than inheriting a promise
    # nobody checked. This is the property that makes a new backend safe by default.
    assert adapter_can_enforce_schema(UndeclaredModel()) is False
    assert adapter_can_enforce_schema(object()) is False

    # And a backend that does declare is believed.
    assert adapter_can_enforce_schema(ScriptedModel(enforces=True)) is True


def test_unenforceable_schema_request_fails_loudly() -> None:
    model = ScriptedModel(good_reply())

    with pytest.raises(SchemaEnforcementUnavailableError) as caught:
        request_grounded_response(model, "a prompt", ALLOWED, require_enforcement=True)

    message = str(caught.value)
    assert "has not declared" in message
    # The refusal has to explain why proceeding would be worse, or someone will just
    # turn it off again.
    assert "indistinguishable" in message
    assert model.prompts == [], "nothing may be sent when enforcement was required"

    # The same request without requiring enforcement proceeds, and records that
    # enforcement was not in effect rather than staying silent about it.
    permitted = request_grounded_response(model, "a prompt", ALLOWED)
    assert permitted.response is not None
    assert permitted.record.enforcement_available is False
    assert permitted.record.enforcement_required is False


def test_invalid_json_is_retried_with_the_validation_error() -> None:
    model = ScriptedModel("this is not json at all", good_reply())

    result = request_grounded_response(model, "ORIGINAL PROMPT", ALLOWED)

    assert result.response is not None
    assert result.record.attempts == 2
    assert result.record.schema_violation == 1

    # The retry must carry the actual complaint. Asking again with an identical prompt
    # and hoping for a different reply is what wastes the budget.
    assert len(model.prompts) == 2
    retry = model.prompts[1]
    assert "ORIGINAL PROMPT" in retry
    assert "REJECTED" in retry
    assert "malformed model JSON" in retry


def test_retry_budget_is_bounded_and_recorded() -> None:
    model = ScriptedModel(*["not json"] * 10)

    result = request_grounded_response(model, "a prompt", ALLOWED, max_attempts=3)

    assert result.response is None
    assert result.record.attempts == 3
    assert result.record.max_attempts == 3
    assert len(model.prompts) == 3, "the budget is a ceiling, not a suggestion"
    assert len(result.record.attempt_log) == 3
    assert [entry.outcome for entry in result.record.attempt_log] == ["schema_violation"] * 3
    assert result.errors, "the reason it gave up is recorded, not swallowed"

    # A default exists so neither run path has to remember to pass one.
    assert DEFAULT_MAX_ATTEMPTS >= 1
    exhausting = ScriptedModel(*["not json"] * (DEFAULT_MAX_ATTEMPTS + 5))
    assert request_grounded_response(exhausting, "p", ALLOWED).record.attempts == (
        DEFAULT_MAX_ATTEMPTS
    )

    with pytest.raises(ValueError, match="at least 1"):
        request_grounded_response(ScriptedModel(), "p", ALLOWED, max_attempts=0)


def test_schema_violation_and_valid_but_unsupported_are_counted_separately() -> None:
    """Two different problems. One number covering both would hide the second."""

    model = ScriptedModel(
        "not json at all",
        cites_something_it_was_never_given(),
        good_reply(),
    )

    result = request_grounded_response(model, "a prompt", ALLOWED)

    assert result.response is not None
    assert result.record.attempts == 3
    assert result.record.schema_violation == 1
    assert result.record.valid_but_unsupported == 1
    assert [entry.outcome for entry in result.record.attempt_log] == [
        "schema_violation",
        "valid_but_unsupported",
        "accepted",
    ]

    # The classifier underneath says which kind, not merely that something went wrong.
    _, kind, _ = classify_grounded_response("not json", ALLOWED)
    assert kind == "schema_violation"
    _, kind, errors = classify_grounded_response(cites_something_it_was_never_given(), ALLOWED)
    assert kind == "valid_but_unsupported"
    assert any("CIT-99" in error for error in errors)
    response, kind, errors = classify_grounded_response(good_reply(), ALLOWED)
    assert kind is None and errors == [] and response is not None


def test_a_schema_valid_answer_that_cites_nothing_is_counted_but_not_rejected() -> None:
    """The failure that grows as models shrink: valid shape, nothing behind it.

    Counting it rather than rejecting it is deliberate. Rejecting would change what
    `ask` accepts, which belongs in its own change; leaving it uncounted would hide it.
    """

    uncited = json.dumps(
        {
            "paragraphs": [
                {"paragraph_id": "P1", "text": "A confident claim.", "citation_ids": []}
            ],
            "claims": [{"claim_id": "C1", "statement": "Another one.", "citation_ids": []}],
        }
    )

    assert (
        count_unsupported_statements(GroundedModelResponse.model_validate(json.loads(uncited))) == 2
    )

    result = request_grounded_response(ScriptedModel(uncited), "a prompt", ALLOWED)
    assert result.response is not None, "still accepted, exactly as before this change"
    assert result.record.unsupported_statements == 2
    assert result.record.schema_violation == 0
    assert result.record.valid_but_unsupported == 0


def test_both_run_paths_record_the_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ask` and `report` do not share the model call, so the wiring is checked twice.

    A fix applied to one and missed on the other is the specific mistake this repository
    warns about, so this drives both against a real archive with real evidence in it and
    reads the counters each one wrote.
    """

    from typer.testing import CliRunner

    from archiv.cli import app
    from archiv.model_adapter import save_model_config
    from archiv.sample_vault import create_sample_vault
    from archiv.tasks import run_task

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    CliRunner().invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(
        ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint="http://127.0.0.1:11434",
            model="a-model",
        ),
        home,
    )

    def scripted_builder(config: object, home_arg: object = None) -> ScriptedModel:
        del config, home_arg
        # One unusable reply, then a usable one, so both paths must retry to succeed.
        return ScriptedModel("not json at all", good_reply())

    monkeypatch.setattr("archiv.grounding.build_model_adapter", scripted_builder)
    monkeypatch.setattr("archiv.tasks.build_model_adapter", scripted_builder)

    from archiv.grounding import run_grounded_ask

    ask_result = run_grounded_ask("unique fixture marker", home=home)
    ask_record = json.loads((Path(ask_result.evidence_dir) / "structured_output.json").read_text())
    assert ask_record["attempts"] == 2
    assert ask_record["schema_violation"] == 1
    assert ask_record["enforcement_available"] is False

    task_path = tmp_path / "task.yaml"
    task_path.write_text(
        json.dumps(
            {
                "task": "cross-file-report",
                "query": "unique fixture marker",
                "render": False,
                # Without this the report never calls a model at all, and the test would
                # pass by checking nothing.
                "model_policy": "configured-local",
            }
        ),
        encoding="utf-8",
    )
    task_result = run_task(task_path, home=home)
    task_record = json.loads(
        (Path(task_result.evidence_dir) / "structured_output.json").read_text()
    )
    assert task_record["attempts"] == 2
    assert task_record["schema_violation"] == 1
    assert task_record["enforcement_available"] is False
