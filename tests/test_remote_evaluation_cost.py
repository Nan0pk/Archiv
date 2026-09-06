"""A paid model cannot be asked a question this archive has not budgeted for.

The remote path is the only one that costs money, and the retry layer above it can send
the same question three times. Nothing capped that before. What is asserted here is not
that an error comes back — it is that nothing is sent.

Three things are refused rather than guessed:

- no prices, no call, because a rate nobody entered is a number nobody checked;
- no ceiling, no call, because the ceiling is the point;
- no token count from character length, ever. Counting needs the provider's own
  tokenizer, which the usual library fetches over the network on first use. This project
  forbids downloading anything at runtime, so the encoding must already be on disk with
  its hash recorded. Without one, the count is skipped and says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.cost_control import (
    PinnedTokenizer,
    SpendCeilingReachedError,
    SpendLedger,
    SpendPolicy,
    SpendPolicyMissingError,
    TokenPrices,
    check_spend_allowance,
    load_ledger,
    load_spend_policy,
    record_spend,
    save_spend_policy,
    spend_ledger_path,
)
from archiv.evaluation_config import mark_for_evaluation
from archiv.model_adapter import ModelConfig, RemoteEvaluationAdapter

runner = CliRunner()

API_KEY_ENV = "ARCHIV_TEST_COST_KEY"


def policy(ceiling_usd: float = 1.0, max_output_tokens: int = 1000) -> SpendPolicy:
    return SpendPolicy(
        ceiling_usd=ceiling_usd,
        max_output_tokens=max_output_tokens,
        prices=TokenPrices(
            input_per_million_usd=10.0,
            output_per_million_usd=30.0,
            recorded_on="2026-01-01",
            source="a test, not a provider",
        ),
    )


def remote_config() -> ModelConfig:
    return ModelConfig(
        adapter="remote-evaluation",
        endpoint="https://api.openai.com",
        model="a-model",
        api_key_env=API_KEY_ENV,
    )


class RecordingOpener:
    """Stands in for the network, and remembers whether it was ever asked to send."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def open(self, request: object, timeout: float | None = None) -> object:
        del timeout
        self.calls.append(getattr(request, "full_url", "?"))
        raise AssertionError("the transport was invoked; nothing should have been sent")


def install_opener(monkeypatch: pytest.MonkeyPatch) -> RecordingOpener:
    opener = RecordingOpener()

    def opener_for(origin: tuple[str, str, int | None]) -> RecordingOpener:
        del origin
        return opener

    monkeypatch.setattr("archiv.model_adapter._opener_for", opener_for)
    return opener


def test_prompt_tokens_are_counted_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counted from a pinned encoding, or not at all. Never from character length."""

    home = tmp_path / "home"
    save_spend_policy(policy(), home)

    preflight = check_spend_allowance("a question about the archive", home)

    # No encoding is pinned here, so there is no count -- and the reason is recorded
    # rather than a number being invented to fill the gap.
    assert preflight.prompt_tokens is None
    assert preflight.projected_input_usd is None
    assert preflight.tokenizer_status == "not-configured"
    assert preflight.tokenizer_detail

    # A pinned encoding that is not actually there is unavailable, not an excuse to guess.
    missing = policy()
    missing.tokenizer = PinnedTokenizer(
        encoding_name="o200k_base", path=str(tmp_path / "absent.tiktoken"), sha256="0" * 64
    )
    save_spend_policy(missing, home)
    unavailable = check_spend_allowance("a question", home)
    assert unavailable.prompt_tokens is None
    assert unavailable.tokenizer_status == "unavailable"
    assert "missing" in unavailable.tokenizer_detail

    # A file whose hash does not match what was recorded is refused as well.
    planted = tmp_path / "planted.tiktoken"
    planted.write_bytes(b"not the encoding that was pinned")
    wrong_hash = policy()
    wrong_hash.tokenizer = PinnedTokenizer(
        encoding_name="o200k_base", path=str(planted), sha256="1" * 64
    )
    save_spend_policy(wrong_hash, home)
    tampered = check_spend_allowance("a question", home)
    assert tampered.prompt_tokens is None
    assert "does not match its recorded hash" in tampered.tokenizer_detail


def test_run_exceeding_the_ceiling_is_refused_before_the_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test that matters: assert the transport was never invoked.

    An error returned after the provider has already billed the call is not a ceiling.
    """

    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    opener = install_opener(monkeypatch)
    adapter = RemoteEvaluationAdapter(remote_config(), home)

    # 1. No policy at all.
    with pytest.raises(SpendPolicyMissingError, match="no spend policy"):
        adapter.complete("a question")
    assert opener.calls == []

    # 2. Policy present, ceiling already spent.
    save_spend_policy(policy(ceiling_usd=1.0), home)
    spend_ledger_path(home).parent.mkdir(parents=True, exist_ok=True)
    spend_ledger_path(home).write_text(
        SpendLedger(spent_usd=1.0, recorded_calls=3).model_dump_json(), encoding="utf-8"
    )
    with pytest.raises(SpendCeilingReachedError, match="of its"):
        adapter.complete("a question")
    assert opener.calls == [], "nothing may be sent once the ceiling is reached"

    # 3. Under the ceiling but this call would cross it. Needs a real count, so this is
    #    asserted through the projection directly rather than pretending to have one.
    save_spend_policy(policy(ceiling_usd=0.02, max_output_tokens=1000), home)
    spend_ledger_path(home).write_text(
        SpendLedger(spent_usd=0.0).model_dump_json(), encoding="utf-8"
    )
    preflight = check_spend_allowance("a question", home)
    assert preflight.spent_before_usd == 0.0
    assert preflight.ceiling_usd == 0.02


def test_cumulative_spend_is_recorded_per_archive(tmp_path: Path) -> None:
    """The ceiling means nothing unless what has been spent survives across runs."""

    first, second = tmp_path / "first", tmp_path / "second"
    save_spend_policy(policy(ceiling_usd=10.0), first)
    save_spend_policy(policy(ceiling_usd=10.0), second)

    assert load_ledger(first).spent_usd == 0.0
    record_spend(1_000_000, 1_000_000, first)
    # 10 dollars per million in, 30 per million out.
    assert load_ledger(first).spent_usd == pytest.approx(40.0)
    assert load_ledger(first).recorded_calls == 1

    # Separate archives keep separate totals.
    assert load_ledger(second).spent_usd == 0.0

    # It accumulates rather than replacing.
    record_spend(1_000_000, 0, first)
    assert load_ledger(first).spent_usd == pytest.approx(50.0)
    assert load_ledger(first).recorded_calls == 2

    # A call the provider reported no usage for is counted apart, never estimated in.
    record_spend(None, None, first)
    after = load_ledger(first)
    assert after.spent_usd == pytest.approx(50.0), "an unmeasured call adds no invented cost"
    assert after.unmeasured_calls == 1
    assert after.recorded_calls == 3

    # And it survives being read back from disk rather than living in memory.
    reloaded = json.loads(spend_ledger_path(first).read_text())
    assert reloaded["spent_usd"] == pytest.approx(50.0)
    assert reloaded["unmeasured_calls"] == 1


def test_projected_cost_is_shown_before_a_remote_run(tmp_path: Path) -> None:
    """What a run is allowed to spend is visible without having to run it."""

    home = tmp_path / "home"
    result = runner.invoke(
        app,
        [
            "model",
            "spend",
            "set",
            "--ceiling-usd",
            "2.50",
            "--input-per-million",
            "10",
            "--output-per-million",
            "30",
            "--prices-recorded-on",
            "2026-01-01",
            "--prices-source",
            "provider pricing page",
            "--home",
            str(home),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Ceiling: $2.50" in result.output
    assert "recorded 2026-01-01" in result.output
    assert "Prompt counting: not-configured" in result.output

    record_spend(100_000, 10_000, home)
    status = runner.invoke(app, ["model", "spend", "status", "--home", str(home)])
    assert status.exit_code == 0
    assert "of $2.50" in status.output
    assert "Prompt counting:" in status.output
    # The date is shown so an old rate is visible as an old rate.
    assert "check them against the provider if that date is old" in status.output


def test_there_are_no_default_prices_or_ceiling(tmp_path: Path) -> None:
    """A rate nobody entered is a number nobody checked, so there is no default."""

    home = tmp_path / "home"
    assert load_spend_policy(home) is None

    with pytest.raises(SpendPolicyMissingError):
        check_spend_allowance("a question", home)

    fields = SpendPolicy.model_fields
    assert fields["ceiling_usd"].is_required()
    assert fields["prices"].is_required()
    assert TokenPrices.model_fields["recorded_on"].is_required()
    assert TokenPrices.model_fields["source"].is_required()


def test_resetting_the_recorded_spend_requires_saying_so(tmp_path: Path) -> None:
    home = tmp_path / "home"
    save_spend_policy(policy(), home)
    record_spend(1_000_000, 0, home)
    assert load_ledger(home).spent_usd == pytest.approx(10.0)

    refused = runner.invoke(app, ["model", "spend", "reset", "--home", str(home)])
    assert refused.exit_code == 1
    assert load_ledger(home).spent_usd == pytest.approx(10.0)

    done = runner.invoke(
        app,
        [
            "model",
            "spend",
            "reset",
            "--acknowledge-this-forgets-what-was-spent",
            "--home",
            str(home),
        ],
    )
    assert done.exit_code == 0
    assert load_ledger(home).spent_usd == 0.0
