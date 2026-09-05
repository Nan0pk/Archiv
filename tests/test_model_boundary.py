"""Regression tripwire for the loopback-only model boundary.

Every assertion here passes against the code as it stands. The point is not to
describe work still to do but to make it impossible for later steps — which add a
separate, explicitly-labelled remote evaluation adapter — to weaken the local
boundary by accident.

Each rejection is pinned at two layers: the constructor a caller reaches directly,
and the persisted `model.json` a caller reaches through `load_model_config`. A
validator that only guards one of the two is not a boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from archiv.model_adapter import (
    DisabledModelAdapter,
    ModelConfig,
    OpenAICompatibleLoopbackAdapter,
    build_model_adapter,
    load_model_config,
    model_config_path,
)

LOOPBACK_ADAPTER = "openai-compatible-loopback"


def _persisted(reason: str, payload: dict[str, object]) -> None:
    """Assert a persisted `model.json` body is refused with the same user-visible reason."""

    with pytest.raises(ValidationError, match=reason):
        ModelConfig.model_validate_json(json.dumps(payload))


def _loopback_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "adapter": LOOPBACK_ADAPTER,
        "endpoint": "http://127.0.0.1:11434",
        "model": "qwen2.5",
    }
    payload.update(overrides)
    return payload


def test_https_endpoint_is_rejected() -> None:
    reason = "must use plain HTTP on loopback"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint="https://127.0.0.1:11434", model="qwen2.5")
    _persisted(reason, _loopback_payload(endpoint="https://127.0.0.1:11434"))

    # A loopback host does not launder a non-HTTP scheme.
    for endpoint in ("https://localhost:11434", "http+unix://127.0.0.1", "ws://127.0.0.1:11434"):
        _persisted(reason, _loopback_payload(endpoint=endpoint))


def test_non_loopback_host_is_rejected() -> None:
    reason = "must resolve explicitly to loopback"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint="http://example.com", model="qwen2.5")
    _persisted(reason, _loopback_payload(endpoint="http://example.com"))

    # Private and link-local addresses are still somebody else's machine.
    for endpoint in (
        "http://10.0.0.5",
        "http://192.168.1.10:11434",
        "http://169.254.169.254",
        "http://127.0.0.1.example.com",
        "http://localhost.example.com",
    ):
        _persisted(reason, _loopback_payload(endpoint=endpoint))


def test_embedded_credentials_query_and_fragment_are_rejected() -> None:
    reason = "must not contain credentials, query, or fragment"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint="http://user:pw@127.0.0.1", model="qwen2.5")
    _persisted(reason, _loopback_payload(endpoint="http://user:pw@127.0.0.1"))

    for endpoint in (
        "http://user@127.0.0.1",
        "http://127.0.0.1/?x=1",
        "http://127.0.0.1?x=1",
        "http://127.0.0.1#frag",
        "http://127.0.0.1/#frag",
    ):
        _persisted(reason, _loopback_payload(endpoint=endpoint))


def test_endpoint_with_api_path_is_rejected() -> None:
    reason = "must be a server root without an API path"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint="http://127.0.0.1/v1", model="qwen2.5")
    _persisted(reason, _loopback_payload(endpoint="http://127.0.0.1/v1"))

    # The adapter appends /v1/chat/completions itself; a caller-supplied path would
    # let a proxy route the request somewhere the host check never saw.
    for endpoint in (
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://localhost:11434/proxy",
        "http://127.0.0.1//",
    ):
        _persisted(reason, _loopback_payload(endpoint=endpoint))


def test_api_key_env_must_be_an_environment_variable_name() -> None:
    reason = "must be a valid environment-variable name"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(
            adapter=LOOPBACK_ADAPTER,
            endpoint="http://127.0.0.1:11434",
            model="qwen2.5",
            api_key_env="not a name",
        )
    for api_key_env in ("not a name", "PATH;rm -rf /", "1KEY", ""):
        _persisted(reason, _loopback_payload(api_key_env=api_key_env))


def test_loopback_adapter_requires_endpoint_and_model() -> None:
    reason = "loopback adapter requires endpoint and model"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint="http://127.0.0.1:11434", model=None)
    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint=None, model="qwen2.5")
    _persisted(reason, {"adapter": LOOPBACK_ADAPTER})


def test_disabled_adapter_forbids_endpoint_model_and_key() -> None:
    reason = "disabled adapter must not declare endpoint, model, or API key"

    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter="disabled", endpoint="http://127.0.0.1:11434")
    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter="disabled", model="qwen2.5")
    with pytest.raises(ValidationError, match=reason):
        ModelConfig(adapter="disabled", api_key_env="ARCHIV_MODEL_KEY")

    for overrides in (
        {"endpoint": "http://127.0.0.1:11434"},
        {"model": "qwen2.5"},
        {"api_key_env": "ARCHIV_MODEL_KEY"},
    ):
        _persisted(reason, {"adapter": "disabled", **overrides})


def test_disabled_adapter_refuses_to_complete_without_fallback() -> None:
    reason = "model use is disabled; Archiv will not select a hidden fallback"

    adapter = build_model_adapter(ModelConfig())
    assert isinstance(adapter, DisabledModelAdapter)
    with pytest.raises(RuntimeError, match=reason):
        adapter.complete("what is in the archive?")

    with pytest.raises(RuntimeError, match=reason):
        DisabledModelAdapter().complete("")


def test_unknown_adapter_literal_is_rejected() -> None:
    """No adapter name exists beyond the two declared ones; extra fields are refused too."""

    with pytest.raises(ValidationError):
        ModelConfig.model_validate_json(json.dumps({"adapter": "openai"}))
    with pytest.raises(ValidationError):
        ModelConfig.model_validate_json(json.dumps({"adapter": "disabled", "allow_remote": True}))


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_the_three_loopback_spellings_are_accepted(host: str) -> None:
    for endpoint in (f"http://{host}:11434", f"http://{host}:11434/", f"http://{host}"):
        config = ModelConfig(adapter=LOOPBACK_ADAPTER, endpoint=endpoint, model="qwen2.5")
        assert config.endpoint == endpoint
        assert isinstance(build_model_adapter(config), OpenAICompatibleLoopbackAdapter)


def test_absent_config_file_means_explicitly_disabled(tmp_path: Path) -> None:
    """Absence is not 'pick something sensible'; it is the disabled adapter."""

    config = load_model_config(tmp_path / "home")
    assert config.adapter == "disabled"
    assert config.endpoint is None
    assert config.model is None
    assert config.api_key_env is None


def test_a_tampered_config_file_is_rejected_on_load(tmp_path: Path) -> None:
    """Editing model.json by hand cannot smuggle a non-loopback endpoint past the boundary."""

    home = tmp_path / "home"
    path = model_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_loopback_payload(endpoint="http://evil.example.com")), encoding="utf-8"
    )

    with pytest.raises(ValidationError, match="must resolve explicitly to loopback"):
        load_model_config(home)
