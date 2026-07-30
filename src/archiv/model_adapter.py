"""Explicit local-model configuration with no hidden provider fallback."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import Field, model_validator

from archiv.contracts import StrictModel
from archiv.storage.layout import ArchivLayout


class ModelConfig(StrictModel):
    """Persisted model policy. Only disabled or loopback HTTP is allowed."""

    schema_version: str = "1"
    adapter: Literal["disabled", "openai-compatible-loopback"] = "disabled"
    endpoint: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_adapter_fields(self) -> ModelConfig:
        if self.adapter == "disabled":
            if any(value is not None for value in (self.endpoint, self.model, self.api_key_env)):
                raise ValueError("disabled adapter must not declare endpoint, model, or API key")
            return self

        if not self.endpoint or not self.model:
            raise ValueError("loopback adapter requires endpoint and model")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "http":
            raise ValueError("local model endpoint must use plain HTTP on loopback")
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("local model endpoint must resolve explicitly to loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "local model endpoint must not contain credentials, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise ValueError("local model endpoint must be a server root without an API path")
        if self.api_key_env is not None and not self.api_key_env.isidentifier():
            raise ValueError("api_key_env must be a valid environment-variable name")
        return self


class ModelAdapter(Protocol):
    """Minimal completion interface used by future optional reasoning steps."""

    def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True, slots=True)
class DisabledModelAdapter:
    """Fail closed when a task asks for a model but the adapter is disabled."""

    def complete(self, prompt: str) -> str:
        del prompt
        raise RuntimeError("model use is disabled; Archiv will not select a hidden fallback")


@dataclass(frozen=True, slots=True)
class OpenAICompatibleLoopbackAdapter:
    """Small loopback-only OpenAI-compatible client using the Python standard library."""

    config: ModelConfig

    def complete(self, prompt: str) -> str:
        endpoint = cast(str, self.config.endpoint).rstrip("/") + "/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key_env:
            value = os.environ.get(self.config.api_key_env)
            if not value:
                raise RuntimeError(
                    f"configured local model API key environment variable is missing: "
                    f"{self.config.api_key_env}"
                )
            headers["Authorization"] = f"Bearer {value}"
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
        ).encode("utf-8")
        request = Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"local model request failed without fallback: {error}") from error
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("local model response lacks choices[0].message.content") from error
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("local model returned empty content")
        return content


def model_config_path(home: Path | None = None) -> Path:
    return ArchivLayout.resolve(home).config / "model.json"


def load_model_config(home: Path | None = None) -> ModelConfig:
    """Load the exact persisted configuration; absence means explicitly disabled."""

    path = model_config_path(home)
    if not path.is_file():
        return ModelConfig()
    return ModelConfig.model_validate_json(path.read_text(encoding="utf-8"))


def save_model_config(config: ModelConfig, home: Path | None = None) -> Path:
    layout = ArchivLayout.resolve(home)
    layout.ensure()
    path = model_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def build_model_adapter(config: ModelConfig) -> ModelAdapter:
    if config.adapter == "disabled":
        return DisabledModelAdapter()
    return OpenAICompatibleLoopbackAdapter(config)
