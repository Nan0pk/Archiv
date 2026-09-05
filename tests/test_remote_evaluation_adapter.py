"""The one path where document text leaves this machine.

Every test here stubs the transport. Nothing in this file opens a real connection, and
the adapter is never given a reachable host, so a regression that started making real
calls would fail rather than quietly succeed against the internet.

The rules being pinned, in order of how much damage breaking them would do:

1. Without the archive's evaluation mark, nothing is sent -- and the refusal happens
   before the API key is even read, let alone before a socket is opened.
2. The API key lives in an environment variable, never in the config file.
3. Failure raises. There is no second host and no quieter model to fall back to.
4. With no network at all, the failure is clean and named, not a hang or a traceback.
5. A redirect to another origin is refused rather than followed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from archiv.evaluation_config import (
    ENV_OVERRIDE,
    EvaluationNotEnabledError,
    mark_for_evaluation,
)
from archiv.model_adapter import (
    REMOTE_EVALUATION_ENDPOINT,
    ModelConfig,
    RemoteEvaluationAdapter,
    build_model_adapter,
    load_model_config,
    save_model_config,
)

API_KEY_ENV = "ARCHIV_TEST_REMOTE_KEY"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OVERRIDE, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)


def remote_config(**overrides: object) -> ModelConfig:
    fields: dict[str, Any] = {
        "adapter": "remote-evaluation",
        "endpoint": REMOTE_EVALUATION_ENDPOINT,
        "model": "a-model-name",
        "api_key_env": API_KEY_ENV,
    }
    fields.update(overrides)
    return ModelConfig(**fields)


class FakeResponse:
    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        del exc


class RecordingOpener:
    """Stands in for the network. Records what would have been sent."""

    def __init__(self, response: object | BaseException) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def open(self, request: Any, timeout: float | None = None) -> Any:
        del timeout
        self.calls.append((request.full_url, dict(request.headers), request.data))
        if isinstance(self.response, BaseException):
            raise self.response
        return FakeResponse(self.response)


def install_opener(
    monkeypatch: pytest.MonkeyPatch, response: object | BaseException
) -> RecordingOpener:
    opener = RecordingOpener(response)

    def opener_for(origin: tuple[str, str, int | None]) -> RecordingOpener:
        del origin
        return opener

    monkeypatch.setattr("archiv.model_adapter._opener_for", opener_for)
    return opener


def unreachable_opener(monkeypatch: pytest.MonkeyPatch) -> RecordingOpener:
    """No network at all, which is what the offline acceptance run actually looks like."""

    return install_opener(monkeypatch, URLError("[Errno -3] Temporary failure in name resolution"))


def working_reply(text: str = "an answer") -> dict[str, object]:
    return {"choices": [{"message": {"content": text}}]}


def test_remote_adapter_requires_the_evaluation_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    opener = install_opener(monkeypatch, working_reply())

    adapter = RemoteEvaluationAdapter(remote_config(), home)
    with pytest.raises(EvaluationNotEnabledError):
        adapter.complete("what is in the archive?")

    # Nothing was sent. This is the assertion that matters most in the file.
    assert opener.calls == []

    mark_for_evaluation(home)
    assert adapter.complete("what is in the archive?") == "an answer"
    assert len(opener.calls) == 1


def test_the_marker_is_checked_before_the_api_key_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unmarked archive refuses for the right reason, not for a missing key."""

    opener = install_opener(monkeypatch, working_reply())
    adapter = RemoteEvaluationAdapter(remote_config(), tmp_path / "home")

    with pytest.raises(EvaluationNotEnabledError):
        adapter.complete("hello")
    assert opener.calls == []


def test_remote_adapter_is_blocked_by_policy_without_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both run paths record a policy refusal, not a generic failure.

    `ask` and `report` do not share the model call, so the guard exists twice and is
    tested twice. A refusal recorded as `failed` would read like a bug in the product
    rather than a boundary doing its job.
    """

    from archiv.contracts import RunStatus
    from archiv.grounding import run_grounded_ask

    home = tmp_path / "home"
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    opener = install_opener(monkeypatch, working_reply())
    save_model_config(remote_config(), home)

    result = run_grounded_ask("anything at all", home=home)
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert opener.calls == []
    assert any("not marked for evaluation" in error for error in result.errors)

    recorded = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert recorded["status"] == "blocked_by_policy"

    # The report path again, because it does not go through run_grounded_ask.
    from archiv.tasks import run_task

    task_path = tmp_path / "task.yaml"
    task_path.write_text(
        json.dumps({"task": "cross-file-report", "query": "anything at all"}),
        encoding="utf-8",
    )
    report = run_task(task_path, home=home)
    assert report.status == RunStatus.BLOCKED_BY_POLICY
    assert opener.calls == []
    assert any("not marked for evaluation" in error for error in report.errors)


def test_remote_adapter_requires_an_api_key_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    mark_for_evaluation(home)
    opener = install_opener(monkeypatch, working_reply())
    adapter = RemoteEvaluationAdapter(remote_config(), home)

    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv(API_KEY_ENV, raising=False)
        else:
            monkeypatch.setenv(API_KEY_ENV, value)
        with pytest.raises(RuntimeError, match=API_KEY_ENV):
            adapter.complete("hello")
        assert opener.calls == [], "no unauthenticated request may be sent"

    # The name of the variable is stored; the value never is.
    path = save_model_config(remote_config(), home)
    assert API_KEY_ENV in path.read_text()
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    assert "a-secret" not in path.read_text()
    assert load_model_config(home).api_key_env == API_KEY_ENV


def test_the_api_key_is_sent_but_never_appears_in_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "sk-do-not-leak-me")

    sending = install_opener(monkeypatch, working_reply())
    RemoteEvaluationAdapter(remote_config(), home).complete("hello")
    url, headers, _ = sending.calls[0]
    assert url == f"{REMOTE_EVALUATION_ENDPOINT}/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-do-not-leak-me"

    failing = unreachable_opener(monkeypatch)
    with pytest.raises(RuntimeError) as caught:
        RemoteEvaluationAdapter(remote_config(), home).complete("hello")
    assert "sk-do-not-leak-me" not in str(caught.value)
    assert failing.calls != []


def test_remote_adapter_fails_closed_with_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the offline acceptance run sees: a named failure, not a hang or a traceback."""

    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    unreachable_opener(monkeypatch)

    adapter = RemoteEvaluationAdapter(remote_config(), home)
    with pytest.raises(RuntimeError) as caught:
        adapter.complete("hello")

    message = str(caught.value)
    assert "without fallback" in message
    assert "api.openai.com" in message
    assert "URLError" in message


def test_no_hidden_fallback_on_a_malformed_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    adapter = RemoteEvaluationAdapter(remote_config(), home)

    malformed: list[dict[str, object]] = [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
    ]
    for reply in malformed:
        install_opener(monkeypatch, reply)
        with pytest.raises(RuntimeError, match="lacks choices"):
            adapter.complete("hello")

    empty_values: list[str | None] = ["", "   ", None]
    for empty in empty_values:
        blank: dict[str, object] = {"choices": [{"message": {"content": empty}}]}
        install_opener(monkeypatch, blank)
        with pytest.raises(RuntimeError, match="empty content"):
            adapter.complete("hello")


def test_a_redirect_to_another_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Following one would send the prompt and the key somewhere configuration never named."""

    from archiv.model_adapter import (
        _origin_of,  # pyright: ignore[reportPrivateUsage]
        _RefuseCrossOriginRedirect,  # pyright: ignore[reportPrivateUsage]
    )

    handler = _RefuseCrossOriginRedirect(_origin_of(REMOTE_EVALUATION_ENDPOINT))
    with pytest.raises(RuntimeError, match="another origin"):
        handler.redirect_request(
            req=None,  # pyright: ignore[reportArgumentType]
            fp=None,
            code=302,
            msg="Found",
            headers=None,
            newurl="https://somewhere-else.example.com/v1/chat/completions",
        )


def test_configuration_rules_for_the_remote_adapter(tmp_path: Path) -> None:
    """A separate rule set from the loopback one, sharing a wire format but not a policy."""

    del tmp_path

    assert remote_config().provenance == "remote-evaluation"
    assert isinstance(build_model_adapter(remote_config()), RemoteEvaluationAdapter)

    for endpoint, reason in (
        ("http://api.openai.com", "must use HTTPS"),
        ("https://127.0.0.1:8443", "must not be loopback"),
        ("https://localhost", "must not be loopback"),
        ("https://user:pw@api.openai.com", "must not contain credentials"),
        ("https://api.openai.com/?x=1", "must not contain credentials"),
        ("https://api.openai.com/v1", "must be a server root"),
    ):
        with pytest.raises(ValueError, match=reason):
            remote_config(endpoint=endpoint)

    with pytest.raises(ValueError, match="requires api_key_env"):
        remote_config(api_key_env=None)
    with pytest.raises(ValueError, match="requires endpoint and model"):
        remote_config(model=None)
    with pytest.raises(ValueError, match="must be a valid environment-variable name"):
        remote_config(api_key_env="not a name")
