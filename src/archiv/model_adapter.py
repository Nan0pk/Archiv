"""Explicit local-model configuration with no hidden provider fallback."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
    urlopen,
)

from pydantic import Field, model_validator

from archiv.contracts import StrictModel
from archiv.cost_control import SpendDecision, check_spend_allowance, record_spend, summarise_spend
from archiv.evaluation_config import check_evaluation_opt_in
from archiv.storage.layout import ArchivLayout

Provenance = Literal["local-loopback", "remote-evaluation"]

REMOTE_EVALUATION_ENDPOINT = "https://api.openai.com"
"""Default base URL for the evaluation stand-in, recorded in docs/plan/DECISIONS.md.

It is a suggested starting point for `archiv model configure-remote-evaluation`, not a
constant the adapter falls back to. Any OpenAI-compatible provider works by passing a
different base URL, which is the point of choosing this wire format.
"""


class ModelConfig(StrictModel):
    """Persisted model policy. Only disabled or loopback HTTP is allowed."""

    schema_version: str = "1"
    adapter: Literal["disabled", "openai-compatible-loopback", "remote-evaluation"] = "disabled"
    endpoint: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    provenance: Provenance = "local-loopback"
    """Where inference happens. Derived from ``adapter``; never taken from input."""

    @model_validator(mode="after")
    def validate_adapter_fields(self) -> ModelConfig:
        return self._apply_adapter_rules()

    def _apply_adapter_rules(self) -> ModelConfig:
        # One branch per adapter literal, and a raise for anything unhandled. A
        # fallthrough here would silently apply the loopback rules to a future
        # adapter -- or, once relaxed to fit one, silently exempt the local
        # adapter from them. The boundary has to hold by construction.
        if self.adapter == "disabled":
            self._validate_disabled()
            return self._derive_provenance("local-loopback")
        if self.adapter == "openai-compatible-loopback":
            self._validate_loopback()
            return self._derive_provenance("local-loopback")
        if self.adapter == "remote-evaluation":
            self._validate_remote_evaluation()
            return self._derive_provenance("remote-evaluation")
        raise ValueError(f"unhandled model adapter: {self.adapter!r}")

    def _validate_disabled(self) -> None:
        if any(value is not None for value in (self.endpoint, self.model, self.api_key_env)):
            raise ValueError("disabled adapter must not declare endpoint, model, or API key")

    def _validate_loopback(self) -> None:
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

    def _validate_remote_evaluation(self) -> None:
        # Deliberately a separate rule set from the loopback one. The two adapters share
        # a wire format, not a URL policy: widening the loopback rules to admit a remote
        # host would destroy the property tests/test_model_boundary.py exists to hold.
        if not self.endpoint or not self.model:
            raise ValueError("remote evaluation adapter requires endpoint and model")
        if not self.api_key_env:
            raise ValueError(
                "remote evaluation adapter requires api_key_env, the name of the "
                "environment variable holding the API key; the key itself is never stored"
            )
        if not self.api_key_env.isidentifier():
            raise ValueError("api_key_env must be a valid environment-variable name")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https":
            raise ValueError("remote evaluation endpoint must use HTTPS")
        if not parsed.hostname:
            raise ValueError("remote evaluation endpoint must name a host")
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "remote evaluation endpoint must not be loopback; use the "
                "openai-compatible-loopback adapter for a model on this machine"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "remote evaluation endpoint must not contain credentials, query, or fragment"
            )
        if parsed.path not in {"", "/"}:
            raise ValueError("remote evaluation endpoint must be a server root without an API path")

    def _derive_provenance(self, derived: Provenance) -> ModelConfig:
        """Set provenance from the adapter, refusing a caller-supplied contradiction."""

        if "provenance" in self.model_fields_set and self.provenance != derived:
            raise ValueError(
                f"provenance is derived from the adapter and must not be supplied: "
                f"adapter {self.adapter!r} implies {derived!r}, not {self.provenance!r}"
            )
        self.provenance = derived
        return self


class ReportedUsage(StrictModel):
    """Token counts the model's own reply reported, and nothing else.

    Both fields absent means the backend reported nothing. That is not zero, and the
    difference matters: the spend ledger counts a call with no reported usage separately
    rather than estimating one, and so does anything measuring the workload.
    """

    schema_version: str = "1"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelAdapter(Protocol):
    """Minimal completion interface used by future optional reasoning steps."""

    def complete(self, prompt: str) -> str: ...

    def usage_reports(self) -> tuple[ReportedUsage, ...]:
        """What the backend said each attempt used, for the life of this adapter.

        An adapter is built once per run, so this is the run's usage -- including the
        retries, which were paid for too. Empty means nothing was reported, which is
        the honest answer for a backend that reports nothing.
        """

        return ()

    def spend_decisions(self) -> tuple[SpendDecision, ...]:
        """Every cost decision this adapter took, in the order it took them.

        One per attempt, because the gate runs per attempt. Empty for an adapter that
        costs nothing to call, which is every adapter but the remote one.
        """

        return ()

    def can_enforce_schema(self) -> bool:
        """Whether this backend can actually hold a reply to a JSON schema.

        Declared rather than assumed, and false unless proven. Swapping the model behind
        Archiv can silently stop enforcement happening at all -- the request is accepted,
        the reply comes back looking fine, and nothing says the constraint was dropped.
        Something that has not demonstrated enforcement must not claim it, because the
        whole point is that the difference is otherwise invisible.
        """

        return False


@dataclass(frozen=True, slots=True)
class DisabledModelAdapter:
    """Fail closed when a task asks for a model but the adapter is disabled."""

    def complete(self, prompt: str) -> str:
        del prompt
        raise RuntimeError("model use is disabled; Archiv will not select a hidden fallback")

    def usage_reports(self) -> tuple[ReportedUsage, ...]:
        return ()

    def spend_decisions(self) -> tuple[SpendDecision, ...]:
        # Nothing to decide: this adapter never opens a connection, so it never spends.
        return ()

    def can_enforce_schema(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class OpenAICompatibleLoopbackAdapter:
    """Small loopback-only OpenAI-compatible client using the Python standard library."""

    config: ModelConfig
    reported: list[ReportedUsage] = field(default_factory=list[ReportedUsage])

    def usage_reports(self) -> tuple[ReportedUsage, ...]:
        return tuple(self.reported)

    def spend_decisions(self) -> tuple[SpendDecision, ...]:
        # A loopback model runs on the machine asking the question. There is no bill,
        # so there is no ceiling and nothing to decide.
        return ()

    def can_enforce_schema(self) -> bool:
        # False, and deliberately so. This adapter sends no schema constraint at all, and
        # the servers it talks to are exactly where enforcement has been observed to be
        # accepted and then silently ignored -- a request answered with a success code
        # while the reply was generated unconstrained. Claiming enforcement here would be
        # an unverified promise about somebody else's server.
        return False

    def probe(self, prompt: str, max_output_tokens: int) -> tuple[str, ReportedUsage | None]:
        """Ask for a reply of at most a given length, and hand back what was reported.

        Only this adapter has it, and only local calibration calls it. Separating the time
        spent reading a prompt from the time spent generating needs two replies of
        different lengths; without a way to ask for a short one there is nothing to
        compare. It is deliberately absent from the remote adapter: a caller choosing its
        own reply length there would be choosing its own bill.
        """

        reply = self.complete(prompt, max_output_tokens=max_output_tokens)
        return reply, self.usage_reports()[-1] if self.reported else None

    def complete(self, prompt: str, max_output_tokens: int | None = None) -> str:
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
        body_fields: dict[str, object] = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if max_output_tokens is not None:
            body_fields["max_tokens"] = max_output_tokens
        payload = json.dumps(body_fields).encode("utf-8")
        request = Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(f"local model request failed without fallback: {error}") from error
        prompt_used, completion_used = _reported_usage(body)
        self.reported.append(
            ReportedUsage(prompt_tokens=prompt_used, completion_tokens=completion_used)
        )
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("local model response lacks choices[0].message.content") from error
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("local model returned empty content")
        return content


class _RefuseCrossOriginRedirect(HTTPRedirectHandler):
    """Follow a redirect only if it stays on the configured origin.

    A redirect to another origin would send the prompt -- and the Authorization header
    -- somewhere the configuration never named. Refusing is the whole point: the policy
    is one configured host, never a fallback and never a second one.
    """

    def __init__(self, allowed_origin: tuple[str, str, int | None]) -> None:
        super().__init__()
        self.allowed_origin = allowed_origin

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        if _origin_of(newurl) != self.allowed_origin:
            raise RuntimeError(
                f"remote evaluation endpoint attempted a redirect to another origin "
                f"({_origin_of(newurl)[1]}); refusing, because configuration named only "
                f"{self.allowed_origin[1]}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # pyright: ignore[reportArgumentType]


def _reported_usage(body: object) -> tuple[int | None, int | None]:
    """Token counts the provider reported, or nothing. Never inferred."""

    if not isinstance(body, dict):
        return None, None
    usage = cast("dict[str, object]", body).get("usage")
    if not isinstance(usage, dict):
        return None, None
    fields = cast("dict[str, object]", usage)
    prompt_used = fields.get("prompt_tokens")
    completion_used = fields.get("completion_tokens")
    return (
        prompt_used if isinstance(prompt_used, int) else None,
        completion_used if isinstance(completion_used, int) else None,
    )


def _origin_of(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return (parsed.scheme, parsed.hostname or "", parsed.port)


def _opener_for(allowed_origin: tuple[str, str, int | None]) -> OpenerDirector:
    """The single seam tests replace, so no test ever opens a real connection."""

    return build_opener(_RefuseCrossOriginRedirect(allowed_origin))


@dataclass(frozen=True, slots=True)
class RemoteEvaluationAdapter:
    """A clearly-labelled non-local model, for exercising the product without a GPU.

    This is the one path where document text leaves the machine, so it refuses unless
    the archive carries the evaluation mark, and it refuses before it does anything
    else. Every failure raises; there is no second host and no quieter model to fall
    back to.
    """

    config: ModelConfig
    home: Path | None = None
    reported: list[ReportedUsage] = field(default_factory=list[ReportedUsage])
    decided: list[SpendDecision] = field(default_factory=list[SpendDecision])

    def usage_reports(self) -> tuple[ReportedUsage, ...]:
        return tuple(self.reported)

    def spend_decisions(self) -> tuple[SpendDecision, ...]:
        return tuple(self.decided)

    def can_enforce_schema(self) -> bool:
        # False for now. This provider does offer a schema-constrained reply mode, but
        # this adapter does not request one and no request has ever been sent to the real
        # service from here, so there is nothing measured to base a `True` on. Flipping
        # it should be a deliberate change that arrives with evidence.
        return False

    def complete(self, prompt: str) -> str:
        # Both pre-flight gates together, before the API key is read and long before a
        # socket is opened: may this archive send anything at all, and can it afford to.
        check_evaluation_opt_in(self.home)
        # Recorded whichever way it goes. The gate runs once per attempt, so a run
        # that was allowed to send and then stopped has two answers, not one.
        preflight = check_spend_allowance(prompt, self.home, record_into=self.decided)

        api_key_env = cast(str, self.config.api_key_env)
        # Stripped, not just checked for emptiness: a key with a stray newline -- the
        # usual result of writing it into a shell profile by hand -- would otherwise be
        # pasted straight into a header value.
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(
                f"remote evaluation API key environment variable is missing or empty: "
                f"{api_key_env}. Archiv will not send an unauthenticated request."
            )

        base = cast(str, self.config.endpoint).rstrip("/")
        endpoint = base + "/v1/chat/completions"
        payload = json.dumps(
            {
                "model": self.config.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                # Sent every time, so the reply's cost is bounded even when the prompt
                # could not be counted and no projection was possible.
                "max_tokens": preflight.max_output_tokens,
            }
        ).encode("utf-8")
        request = Request(  # noqa: S310 - scheme is pinned to https by the validator
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        opener = _opener_for(_origin_of(base))
        try:
            with opener.open(request, timeout=self.config.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            # Reached with no network at all, which is the offline acceptance run: a
            # recorded failure naming the host, not a hang and not a traceback. The
            # error text deliberately carries no header, so the key cannot leak here.
            raise RuntimeError(
                f"remote evaluation request to {_origin_of(base)[1]} failed without "
                f"fallback: {type(error).__name__}: {error}"
            ) from error

        # Recorded before the reply is inspected. A reply that came back unusable was
        # still billed for, and a ledger that only counts the calls that worked
        # understates what the archive has spent.
        prompt_used, completion_used = _reported_usage(body)
        self.reported.append(
            ReportedUsage(prompt_tokens=prompt_used, completion_tokens=completion_used)
        )
        record_spend(prompt_used, completion_used, self.home)

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(
                "remote evaluation response lacks choices[0].message.content"
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("remote evaluation model returned empty content")
        return content


def spend_payload(adapter: object) -> dict[str, object] | None:
    """Every cost decision this adapter took, shaped for a run's evidence, or nothing.

    Written by both run paths after the call, replacing the single decision written
    before it. The one written before exists only so a run refused up front still leaves
    a record; once the call has happened, what actually happened is this.
    """

    decisions = getattr(adapter, "spend_decisions", None)
    if decisions is None:
        return None
    taken = cast("tuple[SpendDecision, ...]", decisions())
    if not taken:
        return None
    return summarise_spend(taken).model_dump(mode="json")


def usage_payload(adapter: object) -> dict[str, object] | None:
    """What this adapter reported using, shaped for a run's evidence, or nothing.

    Written by both run paths. `ask` and `report` do not share the model call, so
    anything that must appear in both has to be threaded twice, and the last time that
    was forgotten the report path silently kept the old behaviour.
    """

    reports = getattr(adapter, "usage_reports", None)
    if reports is None:
        return None
    attempts = cast("tuple[ReportedUsage, ...]", reports())
    if not attempts:
        return None
    return {
        "schema_version": "1",
        "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
    }


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


def describe_model(config: ModelConfig) -> tuple[str, str]:
    """What ran and where, as the words a report and its manifest both show.

    Shared deliberately. Three callers generate reports, and when each derived this for
    itself two of them simply did not, so every report produced outside the task runner
    stated "Model identity: disabled" whatever had actually run.
    """

    if config.adapter == "disabled":
        return "disabled", "none"
    return f"{config.adapter} ({config.model})", config.provenance


def describe_unused_model(config: ModelConfig) -> tuple[str, str]:
    """What to record when this run produced prose without calling a model.

    Three separate paths generate reports without calling a model, and each one that
    derived this for itself got it wrong: two defaulted to "disabled" -- false whenever
    the archive has a model configured -- and the third reported the configured model as
    though it had run, so a deterministic report claimed the sources had been sent
    somewhere they had not. Shared here so there is one answer.

    A genuinely disabled archive is left alone: "disabled" is already true of it. An
    archive with a model configured says so, because a bare "not used" would hide that
    it has one.
    """

    identity, _provenance = describe_model(config)
    if identity == "disabled":
        return identity, "none"
    return f"not used (configured: {identity})", "none"


def build_model_adapter(config: ModelConfig, home: Path | None = None) -> ModelAdapter:
    # Exhaustive for the same reason the validator is: a new adapter literal must
    # fail loudly here rather than inherit the loopback client by fallthrough.
    if config.adapter == "disabled":
        return DisabledModelAdapter()
    if config.adapter == "openai-compatible-loopback":
        return OpenAICompatibleLoopbackAdapter(config)
    if config.adapter == "remote-evaluation":
        # `home` decides which archive's evaluation mark is checked, so it has to
        # travel with the adapter rather than defaulting silently at call time.
        return RemoteEvaluationAdapter(config, home)
    raise ValueError(f"unhandled model adapter: {config.adapter!r}")
