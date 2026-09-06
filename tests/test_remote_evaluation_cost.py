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
    SpendLedgerUnreadableError,
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
        encoding_name="o200k_base",
        path=str(tmp_path / "absent.tiktoken"),
        sha256="0" * 64,
        split_pattern="a pinned rule, pinned with the file",
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
        encoding_name="o200k_base",
        path=str(planted),
        sha256="1" * 64,
        split_pattern="a pinned rule, pinned with the file",
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


def test_an_unreadable_ledger_refuses_rather_than_reading_as_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling must not be re-armed by a file that will not parse.

    Reading an unreadable ledger as zero spent fails open on the one number the ceiling
    depends on, and the next recorded call would then overwrite the real total with that
    zero. `config/` travels in backups, and a ledger written by a later Archiv carrying
    a field this one does not know is unreadable here -- so this is a reachable state,
    not a theoretical one.
    """

    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    opener = install_opener(monkeypatch)
    save_spend_policy(policy(ceiling_usd=1.0), home)
    record_spend(500_000, 0, home)
    assert load_ledger(home).spent_usd == pytest.approx(5.0)

    spend_ledger_path(home).write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(SpendLedgerUnreadableError, match="cannot be read"):
        load_ledger(home)

    # No paid call while the total is unknown, and the transport is never reached.
    adapter = RemoteEvaluationAdapter(remote_config(), home)
    with pytest.raises(SpendLedgerUnreadableError):
        adapter.complete("a question")
    assert opener.calls == []

    # And the corrupt file is left alone rather than replaced by a fresh zero.
    with pytest.raises(SpendLedgerUnreadableError):
        record_spend(1000, 1000, home)
    assert spend_ledger_path(home).read_text() == "{ this is not json"

    # A ledger carrying a field this version does not know is the realistic case.
    spend_ledger_path(home).write_text(
        json.dumps({"schema_version": "1", "spent_usd": 5.0, "a_later_field": True}),
        encoding="utf-8",
    )
    with pytest.raises(SpendLedgerUnreadableError):
        load_ledger(home)


def test_an_oversized_call_is_refused_on_its_projected_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch that refuses a single call for its size, which needs a real count."""

    home = tmp_path / "home"
    save_spend_policy(policy(ceiling_usd=0.02, max_output_tokens=1000), home)

    class FakeEncoding:
        def encode(self, text: str) -> list[int]:
            del text
            return [0] * 100_000

    def fake_loader(policy_arg: SpendPolicy) -> tuple[FakeEncoding, str, str]:
        del policy_arg
        return FakeEncoding(), "pinned", "a stand-in encoding"

    monkeypatch.setattr("archiv.cost_control.load_pinned_tokenizer", fake_loader)

    with pytest.raises(SpendCeilingReachedError, match="would cost up to"):
        check_spend_allowance("a long question", home)

    # A prompt that fits is allowed, and its projection is reported rather than guessed.
    save_spend_policy(policy(ceiling_usd=50.0, max_output_tokens=1000), home)
    allowed = check_spend_allowance("a long question", home)
    assert allowed.prompt_tokens == 100_000
    assert allowed.tokenizer_status == "pinned"
    # 100,000 tokens at $10 per million.
    assert allowed.projected_input_usd == pytest.approx(1.0)
    # Plus at most 1,000 output tokens at $30 per million.
    assert allowed.projected_worst_case_usd == pytest.approx(1.03)


def test_a_billed_call_is_recorded_even_when_its_reply_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reply that came back unusable was still charged for."""

    home = tmp_path / "home"
    mark_for_evaluation(home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    save_spend_policy(policy(ceiling_usd=100.0), home)

    class Response:
        def __init__(self, body: dict[str, object]) -> None:
            self._body = json.dumps(body).encode("utf-8")

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *exc: object) -> None:
            del exc

    class Opener:
        def open(self, request: object, timeout: float | None = None) -> Response:
            del request, timeout
            # A 200 with usage reported, but nothing usable in it.
            return Response(
                {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
            )

    def opener_for(origin: tuple[str, str, int | None]) -> Opener:
        del origin
        return Opener()

    monkeypatch.setattr("archiv.model_adapter._opener_for", opener_for)

    adapter = RemoteEvaluationAdapter(remote_config(), home)
    with pytest.raises(RuntimeError, match="lacks choices"):
        adapter.complete("a question")

    ledger = load_ledger(home)
    assert ledger.recorded_calls == 1, "the provider billed for this, so it is recorded"
    # 1000 in at $10/million plus 500 out at $30/million.
    assert ledger.spent_usd == pytest.approx(0.025)


# --- S07A: a refused run records why it was refused ----------------------------


def spent_out_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An archive with documents, marked for evaluation, whose ceiling is already gone."""

    from archiv.model_adapter import save_model_config
    from archiv.sample_vault import create_sample_vault

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(remote_config(), home)
    mark_for_evaluation(home)
    save_spend_policy(policy(ceiling_usd=1.0), home)
    record_spend(100_000, 0, home)  # $1.00 of a $1.00 ceiling.
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    return home


def test_a_refused_run_records_the_numbers_that_refused_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is the run most worth having evidence for, and it had none.

    The cost record used to be the argument to the check, so when the check refused the
    call nothing was written. What the ceiling was, and what had already been spent,
    reached the person through an error message and were then gone.
    """

    from archiv.grounding import run_grounded_ask

    home = spent_out_archive(tmp_path, monkeypatch)
    opener = install_opener(monkeypatch)

    result = run_grounded_ask("unique fixture marker", home=home)
    assert opener.calls == [], "nothing may be sent once the ceiling is reached"

    recorded = json.loads((Path(result.evidence_dir) / "cost.json").read_text())
    assert recorded["outcome"] == "refused-before-any-request"
    assert recorded["refused_because"] == "ceiling-already-reached"
    preflight = recorded["attempts"][0]["preflight"]
    assert preflight["ceiling_usd"] == pytest.approx(1.0)
    assert preflight["spent_before_usd"] == pytest.approx(1.0)
    # Whether there was a projection at all is part of the record, not an omission.
    assert preflight["tokenizer_status"] == "not-configured"
    assert preflight["prompt_tokens"] is None
    assert "ceiling" in recorded["explanation"]


def test_a_spend_refusal_is_recorded_as_blocked_by_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ceiling doing its job is a boundary, not a bug in the product.

    The evaluation-marker refusal is already recorded this way. A spend refusal arriving
    as a generic failure would read to anyone auditing the runs as Archiv breaking.
    """

    from archiv.contracts import RunStatus
    from archiv.grounding import run_grounded_ask

    home = spent_out_archive(tmp_path, monkeypatch)
    install_opener(monkeypatch)

    result = run_grounded_ask("unique fixture marker", home=home)
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert any("ceiling" in error for error in result.errors)

    on_disk = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert on_disk["status"] == "blocked_by_policy"


def test_the_report_path_records_a_refusal_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`report` re-implements the model call, so it wrote no cost record at all.

    Not a weaker version of the ask path's gap -- a total absence. The ceiling was
    enforced inside the adapter, so a refused report failed with an error and left
    nothing behind saying what it would have cost.
    """

    from archiv.contracts import RunStatus
    from archiv.tasks import run_task

    home = spent_out_archive(tmp_path, monkeypatch)
    opener = install_opener(monkeypatch)

    task_path = tmp_path / "report-task.yaml"
    task_path.write_text(
        json.dumps(
            {
                "task": "cross-file-report",
                "query": "unique fixture marker",
                "render": False,
                "model_policy": "configured-local",
            }
        ),
        encoding="utf-8",
    )

    result = run_task(task_path, home=home)
    assert opener.calls == []
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert any("ceiling" in error for error in result.errors)

    recorded = json.loads((Path(result.evidence_dir) / "cost.json").read_text())
    assert recorded["outcome"] == "refused-before-any-request"
    assert recorded["refused_because"] == "ceiling-already-reached"
    assert recorded["attempts"][0]["preflight"]["spent_before_usd"] == pytest.approx(1.0)


# --- S07B: a run refused part-way through must not record itself as allowed ----


def spent_out_after_one_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An archive whose ceiling one call is enough to exhaust.

    The point is a run that is allowed to send, sends, is billed, and is then refused on
    its retry -- which is the case the run's cost record used to describe as allowed.
    """

    from archiv.model_adapter import save_model_config
    from archiv.sample_vault import create_sample_vault

    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(remote_config(), home)
    mark_for_evaluation(home)
    # A ceiling one reported call spends outright: 100k in at $10/million is $1.00.
    save_spend_policy(policy(ceiling_usd=1.0), home)
    monkeypatch.setenv(API_KEY_ENV, "a-secret")
    return home


def install_billing_opener(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A transport that answers unusably and reports usage that exhausts the ceiling.

    Unusable on purpose: the reply has to fail validation so the retry happens, and the
    retry is the attempt that gets refused.
    """

    sent: list[str] = []

    class Response:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = json.dumps(payload).encode()

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    class Opener:
        def open(self, request: object, timeout: float | None = None) -> Response:
            del timeout
            sent.append(getattr(request, "full_url", "?"))
            return Response(
                {
                    "choices": [{"message": {"content": "not a grounded answer at all"}}],
                    "usage": {"prompt_tokens": 100_000, "completion_tokens": 0},
                }
            )

    def opener_for(origin: tuple[str, str, int | None]) -> Opener:
        del origin
        return Opener()

    monkeypatch.setattr("archiv.model_adapter._opener_for", opener_for)
    return sent


def test_a_run_refused_on_a_retry_does_not_record_itself_as_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record used to describe the decision taken before the first attempt only.

    So a run whose first attempt was allowed and whose second was refused left a cost
    record saying the spending was allowed, beside a result saying the run failed. The
    reason it actually stopped was nowhere but inside an error string.
    """

    from archiv.grounding import run_grounded_ask

    home = spent_out_after_one_call(tmp_path, monkeypatch)
    sent = install_billing_opener(monkeypatch)

    result = run_grounded_ask("unique fixture marker", home=home)

    # One request went out and was billed; the second was refused before being sent.
    assert len(sent) == 1, "the retry must be refused before it reaches the transport"
    assert load_ledger(home).spent_usd == pytest.approx(1.0)

    recorded = json.loads((Path(result.evidence_dir) / "cost.json").read_text())
    assert recorded["outcome"] == "refused-after-spending", (
        "the run spent money and was then stopped; it is neither allowed nor simply refused"
    )
    assert len(recorded["attempts"]) == 2
    assert recorded["attempts"][0]["decision"] == "allowed"
    assert recorded["attempts"][1]["decision"] == "refused"
    assert recorded["refused_because"] == "ceiling-already-reached"
    # And what was already spent when the second attempt was weighed is on the record.
    assert recorded["attempts"][1]["preflight"]["spent_before_usd"] == pytest.approx(1.0)


def test_a_ceiling_reached_part_way_through_ends_as_blocked_by_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A boundary doing its job, whether it stops the first attempt or the third.

    Recorded as a generic failure it reads to anyone auditing the runs as Archiv
    breaking, which is exactly the confusion S07A removed for a refusal up front.
    """

    from archiv.contracts import RunStatus
    from archiv.grounding import run_grounded_ask

    home = spent_out_after_one_call(tmp_path, monkeypatch)
    install_billing_opener(monkeypatch)

    result = run_grounded_ask("unique fixture marker", home=home)
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert any("ceiling" in error for error in result.errors)

    on_disk = json.loads((Path(result.evidence_dir) / "result.json").read_text())
    assert on_disk["status"] == "blocked_by_policy"


def test_a_part_way_refusal_still_reports_that_text_was_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal and the disclosure are independent, and must not be conflated.

    An attempt did reach the model, so the archive's text did leave this machine. A
    refusal arriving afterwards changes nothing about that. Reading the disclosure off
    the ending rather than off what happened is how the banner defects in S05 happened.
    """

    from archiv.grounding import run_grounded_ask

    home = spent_out_after_one_call(tmp_path, monkeypatch)
    install_billing_opener(monkeypatch)

    result = run_grounded_ask("unique fixture marker", home=home)
    assert result.text_may_have_been_sent is True, (
        "an earlier attempt reached the model; a later refusal does not unsend it"
    )

    # The human output has to say so too, not just the record. The command used to
    # suppress the warning for every refusal, on the premise that a refusal means
    # nothing was sent -- so the one run that really did reach outside said nothing.
    # Its own archive, because the run above has already spent this one's ceiling, and
    # a second run against it would be refused up front rather than part-way.
    for_cli = spent_out_after_one_call(tmp_path / "cli", monkeypatch)
    install_billing_opener(monkeypatch)
    shown = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(for_cli)])
    assert "ceiling" in shown.output
    assert "NOT A LOCAL ANSWER" in shown.output, (
        "text reached a model on the first attempt; the person has to be told"
    )

    # And the opposite case still reads the opposite way: refused before anything was
    # sent means nothing was sent.
    fresh = spent_out_archive(tmp_path / "fresh", monkeypatch)
    install_opener(monkeypatch)
    refused_up_front = run_grounded_ask("unique fixture marker", home=fresh)
    assert refused_up_front.text_may_have_been_sent is False
    quiet = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(fresh)])
    assert "NOT A LOCAL ANSWER" not in quiet.output, (
        "nothing was sent, so warning that something was would be its own false claim"
    )


def test_the_report_path_also_ends_a_part_way_refusal_as_blocked_by_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`report` re-implements the model call, so this has to be asserted twice.

    Review found the handler on this path could be deleted with the whole suite still
    green, which means the behaviour was held in place on one path by a test and on the
    other by nothing. That is the exact shape of the trap `CLAUDE.md` records about these
    two paths: anything that must appear in both has to be threaded twice, and a claim
    about "both paths" backed by one test is a claim about one path.
    """

    from archiv.contracts import RunStatus
    from archiv.tasks import run_task

    home = spent_out_after_one_call(tmp_path, monkeypatch)
    sent = install_billing_opener(monkeypatch)

    task_path = tmp_path / "report-task.yaml"
    task_path.write_text(
        json.dumps(
            {
                "task": "cross-file-report",
                "query": "unique fixture marker",
                "render": False,
                "model_policy": "configured-local",
            }
        ),
        encoding="utf-8",
    )

    result = run_task(task_path, home=home)

    assert len(sent) == 1, "the retry must be refused before it reaches the transport"
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert any("ceiling" in error for error in result.errors)

    recorded = json.loads((Path(result.evidence_dir) / "cost.json").read_text())
    assert recorded["outcome"] == "refused-after-spending"
    assert [attempt["decision"] for attempt in recorded["attempts"]] == ["allowed", "refused"]
