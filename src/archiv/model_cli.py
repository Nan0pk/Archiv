"""CLI sub-commands for explicit local model configuration and diagnostics."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import BaseModel

from archiv.calibration import (
    CalibrationInputError,
    Distribution,
    LocalMeasurementRefused,
    Unmeasured,
    calibration_path,
    local_profile_from,
    measure_local_throughput,
    predict_from_calibration,
    run_calibration,
)
from archiv.cost_control import (
    PinnedTokenizer,
    SpendLedgerUnreadableError,
    SpendPolicy,
    TokenPrices,
    load_ledger,
    load_pinned_tokenizer,
    load_spend_policy,
    reset_ledger,
    save_spend_policy,
)
from archiv.evaluation_config import (
    ACKNOWLEDGEMENT,
    clear_evaluation_mark,
    load_evaluation_config,
    mark_for_evaluation,
)
from archiv.hardware_profiles import (
    ProfileError,
    available_profiles,
    save_measured_profile,
)
from archiv.model_adapter import (
    REMOTE_EVALUATION_ENDPOINT,
    ModelConfig,
    build_model_adapter,
    load_model_config,
    save_model_config,
)

model_app = typer.Typer(
    no_args_is_help=True, help="Manage local OpenAI-compatible model configuration."
)
evaluation_app = typer.Typer(
    no_args_is_help=True,
    help="Mark this archive as an evaluation archive, where documents may leave this machine.",
)
model_app.add_typer(evaluation_app, name="evaluation")
spend_app = typer.Typer(
    no_args_is_help=True,
    help="What this archive may spend on a model outside this machine, and what it has.",
)
model_app.add_typer(spend_app, name="spend")


def _emit_json(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


@model_app.command("status")
def model_status_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the currently configured local model adapter status."""

    config = load_model_config(home)
    if json_output:
        _emit_json(config)
        return

    typer.echo(f"Adapter: {config.adapter}")
    if config.adapter == "disabled":
        typer.echo("Status: disabled")
    else:
        typer.echo(f"Endpoint: {config.endpoint}")
        typer.echo(f"Model: {config.model}")
        typer.echo(f"API key env: {config.api_key_env or 'none'}")
        typer.echo(f"Timeout: {config.timeout_seconds}s")


@model_app.command("show")
def model_show_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
) -> None:
    """Show the exact persisted model policy; absence means disabled."""

    _emit_json(load_model_config(home))


@model_app.command("configure")
def model_configure_command(
    endpoint: Annotated[
        str,
        typer.Option(
            "--endpoint",
            help="HTTP endpoint bound explicitly to loopback (e.g. http://127.0.0.1:11434).",
        ),
    ],
    model: Annotated[
        str,
        typer.Option("--model", help="Target model identifier (e.g. llama3)."),
    ],
    api_key_env: Annotated[
        str | None,
        typer.Option(
            "--api-key-env",
            help="Optional environment variable name containing the bearer token.",
        ),
    ] = None,
    timeout: Annotated[
        int,
        typer.Option("--timeout", min=1, max=3600, help="Request timeout in seconds."),
    ] = 120,
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Configure an OpenAI-compatible HTTP model server bound to loopback."""

    try:
        config = ModelConfig(
            adapter="openai-compatible-loopback",
            endpoint=endpoint,
            model=model,
            api_key_env=api_key_env,
            timeout_seconds=timeout,
        )
        saved_path = save_model_config(config, home)
    except Exception as error:
        typer.echo(f"configuration failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    payload = {
        "schema_version": "1",
        "status": "configured",
        "config_path": str(saved_path),
        "config": config.model_dump(mode="json"),
    }
    if json_output:
        _emit_json(payload)
        return

    typer.echo(f"Configured local model: {config.model}")
    typer.echo(f"Endpoint: {config.endpoint}")
    typer.echo(f"Config saved to: {saved_path}")


@model_app.command("configure-loopback")
def model_configure_loopback_command(
    endpoint: Annotated[str, typer.Option("--endpoint")],
    model: Annotated[str, typer.Option("--model")],
    api_key_env: Annotated[str | None, typer.Option("--api-key-env")] = None,
    timeout_seconds: Annotated[int, typer.Option("--timeout", min=1, max=3600)] = 120,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False)] = None,
) -> None:
    """Configure a loopback-only OpenAI-compatible endpoint without fallback."""

    config = ModelConfig(
        adapter="openai-compatible-loopback",
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
    )
    path = save_model_config(config, home)
    _emit_json({"config_path": str(path), "config": config.model_dump(mode="json")})


@model_app.command("profiles")
def model_profiles_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List the hardware a prediction can be made for, and where each figure came from."""

    try:
        profiles = available_profiles(home)
    except ProfileError as error:
        typer.echo(f"cannot read the hardware profiles: {error}", err=True)
        raise typer.Exit(code=2) from error

    if json_output:
        _emit_json([profile.model_dump(mode="json") for profile in profiles])
        return

    for profile in profiles:
        prefill = profile.prefill_tokens_per_second
        decode = profile.decode_tokens_per_second
        typer.echo(f"{profile.id}")
        typer.echo(f"  {profile.hardware}")
        typer.echo(f"  {profile.model} ({profile.quantisation}), {profile.backend}")
        typer.echo(
            f"  Reads a prompt at {prefill.low}-{prefill.high} tokens a second; "
            f"generates at {decode.low}-{decode.high}"
        )
        if profile.confidence == "measured":
            typer.echo(f"  Measured on: {profile.measured_on_machine}")
        else:
            typer.echo(f"  Published figure: {profile.source_url} (read {profile.retrieved_on})")
        typer.echo("")


@model_app.command("calibrate")
def model_calibrate_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    benchmark: Annotated[
        Path | None,
        typer.Option("--benchmark", dir_okay=False, help="The frozen benchmark to ask from."),
    ] = None,
    questions: Annotated[
        Path | None,
        typer.Option(
            "--questions",
            dir_okay=False,
            help="A JSON list of questions, for an archive the frozen benchmark does not fit.",
        ),
    ] = None,
    quality_from: Annotated[
        Path | None,
        typer.Option(
            "--quality-from",
            dir_okay=False,
            help="A field-trial results file whose scores are recorded beside these timings.",
        ),
    ] = None,
    evidence_limit: Annotated[int, typer.Option("--evidence-limit", min=1, max=50)] = 8,
    predict_for: Annotated[
        str | None,
        typer.Option(
            "--predict-for",
            help="A hardware profile id to predict this workload's wall clock on.",
        ),
    ] = None,
    local: Annotated[
        bool,
        typer.Option(
            "--local",
            help="Measure this machine's own speed and record it as a measured profile.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Measure what a question costs here, and how the configured model answers it."""

    try:
        calibration = run_calibration(
            home=home,
            benchmark=benchmark,
            questions=questions,
            quality_from=quality_from,
            evidence_limit=evidence_limit,
        )
    except CalibrationInputError as error:
        typer.echo(f"calibration could not start: {error}", err=True)
        raise typer.Exit(code=2) from error

    prediction = None
    if local:
        # Measured first, so a prediction asked for in the same breath uses this
        # machine's own figures rather than somebody else's published ones.
        try:
            measured = measure_local_throughput(
                home=home, prompt="Summarise the evidence you were given, with citations."
            )
        except LocalMeasurementRefused as error:
            typer.echo(f"this machine's speed was not measured: {error}", err=True)
            raise typer.Exit(code=2) from error
        saved = save_measured_profile(local_profile_from(measured), home)
        typer.echo(
            f"Measured on this machine: {measured.prefill_tokens_per_second} tokens a second "
            f"reading a prompt, {measured.decode_tokens_per_second} generating. "
            f"Recorded in {saved}"
        )
        predict_for = predict_for or "this-machine"

    if predict_for is not None:
        try:
            prediction = predict_from_calibration(calibration, predict_for, home=home)
        except ProfileError as error:
            typer.echo(f"no prediction: {error}", err=True)
            raise typer.Exit(code=2) from error

    if json_output:
        payload = calibration.model_dump(mode="json")
        payload["prediction"] = (
            prediction.model_dump(mode="json") if prediction is not None else None
        )
        _emit_json(payload)
        return

    workload = calibration.workload
    typer.echo(f"Calibration {calibration.calibration_id}")
    typer.echo(f"  Questions asked: {workload.question_count}, from {calibration.question_source}")
    typer.echo(
        f"  Answered: {calibration.model.questions_answered}"
        f", not answered: {calibration.model.questions_not_answered}"
    )
    typer.echo(f"  Model: {calibration.model.identity}")
    typer.echo(f"  Median retrieval: {workload.retrieval_ms.median} ms")
    if isinstance(calibration.model.wall_clock_ms, Distribution):
        typer.echo(
            f"  Median question: {calibration.model.wall_clock_ms.median} ms"
            f" over {calibration.model.questions_where_a_model_ran} questions a model ran on"
        )
    else:
        typer.echo(f"  Question time: not measured -- {calibration.model.wall_clock_ms.reason}")
    if isinstance(workload.completion_tokens, Unmeasured):
        typer.echo(f"  Reply length: not measured -- {workload.completion_tokens.reason}")
    else:
        typer.echo(f"  Median reply: {workload.completion_tokens.median} tokens")
    if isinstance(calibration.quality, Unmeasured):
        typer.echo(f"  Answer quality: not measured -- {calibration.quality.reason}")
    else:
        typer.echo(
            "  Answer quality: recall "
            f"{calibration.quality.mean_recall_at_evidence_limit}"
            f", fabricated citations {calibration.quality.fabricated_identifier_count}"
        )
    if prediction is not None:
        typer.echo("")
        typer.echo(f"Predicted on {prediction.hardware}")
        typer.echo(f"  {prediction.model} ({prediction.quantisation})")
        typer.echo(
            f"  Before the first word: {prediction.time_to_first_token.middle_ms / 1000:.1f} s"
            f" (between {prediction.time_to_first_token.low_ms / 1000:.1f}"
            f" and {prediction.time_to_first_token.high_ms / 1000:.1f})"
        )
        typer.echo(
            f"  Writing the answer: {prediction.generation.middle_ms / 1000:.1f} s"
            f" (between {prediction.generation.low_ms / 1000:.1f}"
            f" and {prediction.generation.high_ms / 1000:.1f})"
        )
        typer.echo(
            f"  Whole question: {prediction.total.middle_ms / 1000:.1f} s"
            f" (between {prediction.total.low_ms / 1000:.1f}"
            f" and {prediction.total.high_ms / 1000:.1f})"
        )
        if prediction.label == "measured":
            typer.echo(f"  Measured on: {prediction.measured_on_machine}")
        else:
            typer.echo(
                f"  Estimated, plus or minus {prediction.band_fraction * 100:.0f}%, from "
                f"{prediction.source_url} read on {prediction.retrieved_on}"
            )
        if prediction.anchor_disagreement_note:
            typer.echo(f"  {prediction.anchor_disagreement_note}")
    typer.echo(f"  Written to: {calibration_path(home, calibration.calibration_id)}")


@model_app.command("test")
def model_test_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Test connectivity to the configured local model server."""

    config = load_model_config(home)
    if config.adapter == "disabled":
        payload = {
            "schema_version": "1",
            "status": "disabled",
            "message": (
                "Model adapter is disabled; configure a loopback endpoint to test connectivity."
            ),
        }
        if json_output:
            _emit_json(payload)
        else:
            typer.echo("Model adapter is disabled.")
        raise typer.Exit(code=1)

    start_time = time.monotonic()
    adapter = build_model_adapter(config, home)
    try:
        response = adapter.complete("Respond with the single word PONG.")
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        success_payload: dict[str, object] = {
            "schema_version": "1",
            "status": "succeeded",
            "latency_ms": duration_ms,
            "endpoint": config.endpoint,
            "model": config.model,
            "response": response.strip(),
        }
        if json_output:
            _emit_json(success_payload)
            return
        typer.echo(f"Connectivity test succeeded ({duration_ms} ms)")
        typer.echo(f"Endpoint: {config.endpoint}")
        typer.echo(f"Model: {config.model}")
    except Exception as error:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        failed_payload: dict[str, object] = {
            "schema_version": "1",
            "status": "failed",
            "latency_ms": duration_ms,
            "endpoint": config.endpoint,
            "model": config.model,
            "error": str(error),
        }
        if json_output:
            _emit_json(failed_payload)
        else:
            typer.echo(f"Connectivity test failed ({duration_ms} ms): {error}", err=True)
        raise typer.Exit(code=1) from error


@model_app.command("disable")
def model_disable_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Disable local model integration."""

    config = ModelConfig(adapter="disabled")
    saved_path = save_model_config(config, home)
    payload = {
        "schema_version": "1",
        "status": "disabled",
        "config_path": str(saved_path),
        "config": config.model_dump(mode="json"),
    }
    if json_output:
        _emit_json(payload)
        return

    typer.echo(f"Model integration disabled. Config saved to: {saved_path}")


@evaluation_app.command("status")
def evaluation_status_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show whether this archive is marked for evaluation, and what was agreed to."""

    config = load_evaluation_config(home)
    if json_output:
        _emit_json(config)
        return

    if not config.evaluation:
        typer.echo("Evaluation: not marked")
        typer.echo("Nothing in this archive is sent to a model outside this machine.")
        return

    typer.echo("Evaluation: MARKED")
    typer.echo("Documents in this archive may be sent to a model outside this machine.")
    typer.echo(f"Agreed at: {config.acknowledged_at}")
    typer.echo(f"Agreed to: {config.acknowledgement}")


@evaluation_app.command("enable")
def evaluation_enable_command(
    acknowledge: Annotated[
        bool,
        typer.Option(
            "--acknowledge-documents-leave-this-machine",
            help="Required. Confirms you understand documents are sent off this machine.",
        ),
    ] = False,
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark this archive for evaluation. Requires the explicit acknowledgement flag."""

    # Deliberately not a bare --yes. The flag has to name the consequence, so it cannot
    # be typed by reflex or copied from an unrelated command.
    if not acknowledge:
        typer.echo(ACKNOWLEDGEMENT, err=True)
        typer.echo("", err=True)
        typer.echo(
            "Re-run with --acknowledge-documents-leave-this-machine to confirm.",
            err=True,
        )
        raise typer.Exit(code=1)

    config = mark_for_evaluation(home)
    if json_output:
        _emit_json(config)
        return
    typer.echo("This archive is now marked for evaluation.")
    typer.echo("Documents in it may be sent to a model running outside this machine.")
    typer.echo(f"Agreed at: {config.acknowledged_at}")


@evaluation_app.command("disable")
def evaluation_disable_command(
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Remove the evaluation mark, so nothing may be sent off this machine again."""

    config = clear_evaluation_mark(home)
    if json_output:
        _emit_json(config)
        return
    typer.echo("Evaluation mark removed.")
    typer.echo("Nothing in this archive will be sent to a model outside this machine.")


@model_app.command("configure-remote-evaluation")
def model_configure_remote_evaluation_command(
    model: Annotated[str, typer.Option("--model", help="Model name at the provider.")],
    api_key_env: Annotated[
        str,
        typer.Option(
            "--api-key-env",
            help="Name of the environment variable holding the API key. The key itself "
            "is never written to disk.",
        ),
    ],
    endpoint: Annotated[
        str,
        typer.Option("--endpoint", help="Provider base URL, without an API path."),
    ] = REMOTE_EVALUATION_ENDPOINT,
    timeout: Annotated[int, typer.Option("--timeout", min=1, max=3600)] = 120,
    home: Annotated[
        Path | None,
        typer.Option("--home", file_okay=False, resolve_path=True),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Point Archiv at a model outside this machine, for evaluation without local hardware.

    This only records the policy. Nothing is sent anywhere unless the archive also
    carries the evaluation mark from `archiv model evaluation enable`.
    """

    try:
        config = ModelConfig(
            adapter="remote-evaluation",
            endpoint=endpoint,
            model=model,
            api_key_env=api_key_env,
            timeout_seconds=timeout,
        )
        saved_path = save_model_config(config, home)
    except Exception as error:
        typer.echo(f"configuration failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    payload = {
        "schema_version": "1",
        "status": "configured",
        "config_path": str(saved_path),
        "config": config.model_dump(mode="json"),
    }
    if json_output:
        _emit_json(payload)
        return

    typer.echo(f"Configured a model outside this machine: {config.model}")
    typer.echo(f"Endpoint: {config.endpoint}")
    typer.echo(f"API key read at call time from: {config.api_key_env}")
    typer.echo(f"Config saved to: {saved_path}")
    typer.echo("")
    typer.echo(
        "Nothing is sent anywhere until this archive is also marked for evaluation with "
        "'archiv model evaluation enable'."
    )


@spend_app.command("set")
def model_spend_set_command(
    ceiling_usd: Annotated[
        float,
        typer.Option("--ceiling-usd", min=0.000001, help="Total this archive may spend."),
    ],
    input_per_million: Annotated[
        float,
        typer.Option("--input-per-million", min=0, help="Provider's input price per million."),
    ],
    output_per_million: Annotated[
        float,
        typer.Option("--output-per-million", min=0, help="Provider's output price per million."),
    ],
    prices_recorded_on: Annotated[
        str,
        typer.Option("--prices-recorded-on", help="Date you read those prices, YYYY-MM-DD."),
    ],
    prices_source: Annotated[
        str,
        typer.Option("--prices-source", help="Where you read them, so the figure is checkable."),
    ],
    max_output_tokens: Annotated[int, typer.Option("--max-output-tokens", min=1, max=32000)] = 2048,
    tokenizer_encoding: Annotated[str | None, typer.Option("--tokenizer-encoding")] = None,
    tokenizer_path: Annotated[Path | None, typer.Option("--tokenizer-path", dir_okay=False)] = None,
    tokenizer_sha256: Annotated[str | None, typer.Option("--tokenizer-sha256")] = None,
    tokenizer_split_pattern: Annotated[
        str | None,
        typer.Option(
            "--tokenizer-split-pattern",
            help="The encoding's own split rule. Pinned with it, never assumed.",
        ),
    ] = None,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False, resolve_path=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record what this archive may spend, and what the provider charges.

    Prices have no defaults on purpose: a rate nobody entered is a number nobody
    checked, and published prices change.

    The tokenizer is optional and, if given, must be a file already on disk with its hash
    recorded. Archiv never downloads one. Without it a prompt cannot be counted, so no
    cost is projected before a call — the ceiling still holds on what has been spent and
    on the output cap.
    """

    pinned: PinnedTokenizer | None = None
    given = [tokenizer_encoding, tokenizer_path, tokenizer_sha256, tokenizer_split_pattern]
    if any(value is not None for value in given):
        if not all(value is not None for value in given):
            typer.echo(
                "a pinned tokenizer needs all four of --tokenizer-encoding, "
                "--tokenizer-path, --tokenizer-sha256 and --tokenizer-split-pattern",
                err=True,
            )
            raise typer.Exit(code=1)
        pinned = PinnedTokenizer(
            encoding_name=cast(str, tokenizer_encoding),
            path=str(cast(Path, tokenizer_path)),
            sha256=cast(str, tokenizer_sha256),
            split_pattern=cast(str, tokenizer_split_pattern),
        )

    try:
        policy = SpendPolicy(
            ceiling_usd=ceiling_usd,
            max_output_tokens=max_output_tokens,
            prices=TokenPrices(
                input_per_million_usd=input_per_million,
                output_per_million_usd=output_per_million,
                recorded_on=prices_recorded_on,
                source=prices_source,
            ),
            tokenizer=pinned,
        )
    except Exception as error:
        typer.echo(f"spend policy rejected: {error}", err=True)
        raise typer.Exit(code=1) from error

    path = save_spend_policy(policy, home)
    if json_output:
        _emit_json({"config_path": str(path), "policy": policy.model_dump(mode="json")})
        return
    typer.echo(f"Ceiling: ${policy.ceiling_usd:.2f}")
    typer.echo(
        f"Prices: ${policy.prices.input_per_million_usd}/million in, "
        f"${policy.prices.output_per_million_usd}/million out "
        f"(recorded {policy.prices.recorded_on} from {policy.prices.source})"
    )
    typer.echo(f"Reply capped at: {policy.max_output_tokens} tokens")
    _, status, detail = load_pinned_tokenizer(policy)
    typer.echo(f"Prompt counting: {status} — {detail}")
    typer.echo(f"Saved to: {path}")


@spend_app.command("status")
def model_spend_status_command(
    home: Annotated[Path | None, typer.Option("--home", file_okay=False, resolve_path=True)] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the ceiling, what has been spent against it, and whether prompts can be counted."""

    policy = load_spend_policy(home)
    try:
        ledger = load_ledger(home)
    except SpendLedgerUnreadableError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    if policy is None:
        payload: dict[str, object] = {
            "policy": None,
            "ledger": ledger.model_dump(mode="json"),
        }
        if json_output:
            _emit_json(payload)
            return
        typer.echo("No spend policy. A model outside this machine will be refused.")
        typer.echo("Set one with 'archiv model spend set --help'.")
        return

    _, status, detail = load_pinned_tokenizer(policy)
    if json_output:
        _emit_json(
            {
                "policy": policy.model_dump(mode="json"),
                "ledger": ledger.model_dump(mode="json"),
                "prompt_counting": {"status": status, "detail": detail},
            }
        )
        return
    typer.echo(f"Spent: ${ledger.spent_usd:.4f} of ${policy.ceiling_usd:.2f}")
    typer.echo(f"Calls recorded: {ledger.recorded_calls}")
    if ledger.unmeasured_calls:
        typer.echo(
            f"Of those, {ledger.unmeasured_calls} reported no usage, so their cost is "
            "not in the total above."
        )
    typer.echo(f"Prompt counting: {status} — {detail}")
    typer.echo(
        f"Prices recorded {policy.prices.recorded_on} from {policy.prices.source}; "
        "check them against the provider if that date is old."
    )


@spend_app.command("reset")
def model_spend_reset_command(
    acknowledge: Annotated[
        bool,
        typer.Option(
            "--acknowledge-this-forgets-what-was-spent",
            help="Required. The recorded total is evidence; clearing it discards that.",
        ),
    ] = False,
    home: Annotated[Path | None, typer.Option("--home", file_okay=False, resolve_path=True)] = None,
) -> None:
    """Clear the recorded spend. Use only when it no longer matches what you are billed."""

    if not acknowledge:
        typer.echo(
            "This discards the record of what this archive has spent, which is the only "
            "thing making the ceiling mean anything across runs. Re-run with "
            "--acknowledge-this-forgets-what-was-spent to confirm.",
            err=True,
        )
        raise typer.Exit(code=1)
    reset_ledger(home)
    typer.echo("Recorded spend cleared. The ceiling now applies to a fresh total.")
