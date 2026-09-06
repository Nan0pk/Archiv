"""What a question costs here, measured -- and what could not be measured, said plainly.

The distinction this step is built around is that time can be predicted later and quality
can only ever be measured. So these tests care about two things above everything else:

- that a number is either measured or absent with a reason, never filled in with
  something plausible;
- that nothing measured about one model is presented as a forecast about another.

Nothing here reaches a network. The model is a stand-in, so what is exercised is the
measuring, not a provider.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from archiv.calibration import (
    CalibrationInputError,
    Distribution,
    LocalMeasurementRefused,
    Unmeasured,
    load_questions,
    local_profile_from,
    measure_local_throughput,
    predict_from_calibration,
    run_calibration,
)
from archiv.cli import app
from archiv.hardware_profiles import Unavailable as HardwareUnavailable
from archiv.model_adapter import ModelConfig, ReportedUsage, save_model_config
from archiv.sample_vault import create_sample_vault

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks/field_trial/benchmark.json"
PUBLIC_RESULTS = ROOT / "benchmarks/field_trial/public-results.json"

runner = CliRunner()


class StubAdapter:
    """A model that answers instantly and reports its usage, like a real one does."""

    def __init__(self, reply: str, usage: ReportedUsage | None) -> None:
        self.reply = reply
        self.reported = [usage] if usage is not None else []

    def complete(self, prompt: str) -> str:
        del prompt
        return self.reply

    def can_enforce_schema(self) -> bool:
        return False

    def usage_reports(self) -> tuple[ReportedUsage, ...]:
        return tuple(self.reported)


def grounded_reply() -> str:
    return json.dumps(
        {
            "answer": "A cited answer.",
            "citations": ["CIT-1"],
            "insufficient_evidence": [],
        }
    )


def install_stub(monkeypatch: pytest.MonkeyPatch, usage: ReportedUsage | None = None) -> None:
    """Replace the adapter the ask path builds. No test here opens a connection."""

    def builder(config: ModelConfig, home: Path | None = None) -> StubAdapter:
        del config, home
        return StubAdapter(grounded_reply(), usage)

    monkeypatch.setattr("archiv.grounding.build_model_adapter", builder)


def local_config() -> ModelConfig:
    return ModelConfig(
        adapter="openai-compatible-loopback",
        endpoint="http://127.0.0.1:11434",
        model="a-stand-in-model",
    )


def prepared_archive(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    save_model_config(local_config(), home)
    return home


def question_file(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(["unique fixture marker", "what does the vault record"]),
        encoding="utf-8",
    )
    return path


def test_calibrate_emits_a_workload_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part that stays true when the model changes, which is what S09 predicts from.

    Retrieval time and the size of the work are properties of the archive and the
    question, not of whatever answered it.
    """

    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))

    calibration = run_calibration(home=home, questions=question_file(tmp_path))
    workload = calibration.workload

    assert workload.question_count == 2
    assert workload.evidence_limit == 8
    assert len(workload.questions) == 2
    assert workload.retrieval_ms.count == 2
    assert workload.retrieval_ms.minimum <= workload.retrieval_ms.median
    assert workload.retrieval_ms.median <= workload.retrieval_ms.maximum

    # Token counts come from what the model itself reported, never from anything derived.
    assert isinstance(workload.prompt_tokens, Distribution)
    assert isinstance(workload.completion_tokens, Distribution)
    assert workload.prompt_tokens.total == 1800
    assert workload.completion_tokens.total == 240
    assert workload.prompt_token_source == "the provider's own reported usage"

    for row in workload.questions:
        assert row.retrieval_ms >= 0
        assert row.ask_wall_clock_ms >= 0
        assert row.a_model_ran
        assert row.prompt_tokens == 900
        assert row.completion_tokens == 120

    # And it is on disk, not only in the returned object.
    written = json.loads(
        (
            home / "runs" / "calibration" / calibration.calibration_id / "calibration.json"
        ).read_text()
    )
    assert written["workload"]["question_count"] == 2


def test_calibration_json_carries_provenance_and_honest_negatives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A number with no commit behind it cannot be compared with the next one.

    And a number that could not be produced is written down as one that could not be
    produced. The specific case here is time to first token: the adapter asks for a
    whole reply rather than a stream, so prompt processing and generation cannot be told
    apart, and saying a rate anyway would be inventing it.
    """

    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))

    calibration = run_calibration(home=home, questions=question_file(tmp_path))

    assert calibration.schema_version == "1"
    assert calibration.calibration_id
    # Either the commit these numbers were measured at, or a stated reason there is none.
    assert isinstance(calibration.measured_head_sha, str | Unmeasured)
    if isinstance(calibration.measured_head_sha, str):
        assert len(calibration.measured_head_sha) == 40
    else:
        assert calibration.measured_head_sha.reason
    # Where it ran, in terms that describe the kind of machine and not the machine.
    assert calibration.measured_where.platform
    assert calibration.measured_where.python_version

    model = calibration.model
    assert isinstance(model.time_to_first_token_ms, Unmeasured)
    assert model.time_to_first_token_ms.status == "not_measurable"
    assert "stream" in model.time_to_first_token_ms.reason
    assert isinstance(model.prefill_tokens_per_second, Unmeasured)
    assert isinstance(model.decode_tokens_per_second, Unmeasured)
    # The rate that can be measured without a first-token time is named for what it is.
    assert isinstance(model.completion_tokens_per_second_over_the_whole_question, Distribution)

    # Quality was not asked for on this run, so it says so rather than being missing.
    assert isinstance(calibration.quality, Unmeasured)
    assert "field trial" in calibration.quality.reason

    payload = json.loads(
        (
            home / "runs" / "calibration" / calibration.calibration_id / "calibration.json"
        ).read_text()
    )
    assert payload["model"]["time_to_first_token_ms"]["status"] == "not_measurable"


def test_calibration_output_is_redacted_through_the_private_key_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A calibration run against a private archive must be safe to hand to someone.

    The field trial already decides what is private, and this is checked against that
    decision rather than a second opinion: the artefact goes through the harness's own
    redaction and must come back unchanged. If it changes, it was carrying something the
    project has already said must not leave.
    """

    sys.path.insert(0, str(ROOT / "scripts"))
    monkeypatch.setattr(sys, "path", sys.path)
    module = importlib.import_module("field_trial.runner")
    redact_private: Any = module.redact_private

    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))
    # Scored results are passed in on purpose. An earlier version of this test left them
    # out, and the test that did pass them never ran redaction -- so between them the two
    # tests covered everything except the field that was leaking a local path.
    calibration = run_calibration(
        home=home, questions=question_file(tmp_path), quality_from=PUBLIC_RESULTS
    )

    payload = calibration.model_dump(mode="json")
    assert redact_private(payload, [str(home)]) == payload
    assert not isinstance(calibration.quality, Unmeasured)
    assert calibration.quality.scored_by == "public-results.json"
    assert "/" not in calibration.quality.scored_by
    assert len(calibration.quality.scored_by_sha256) == 64

    # And said directly, because the check above passes for a file that simply has no
    # such key: no question text reaches the artefact at all.
    serialized = json.dumps(payload)
    assert "unique fixture marker" not in serialized
    assert str(home) not in serialized
    for row in calibration.workload.questions:
        assert row.question_id.startswith("Q")


def test_quality_gates_are_measured_never_predicted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quality is copied from the scorer, or it is absent. It is never worked out here.

    Two models three points apart on a general capability score were nearly thirty apart
    on grounding, so a quality number carried across from one model to another is not a
    weak estimate -- it is a fabrication. This asserts the artefact cannot express one.
    """

    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))

    scored = run_calibration(
        home=home, questions=question_file(tmp_path), quality_from=PUBLIC_RESULTS
    )
    gates = scored.quality
    assert isinstance(gates, Distribution) is False
    assert not isinstance(gates, Unmeasured)
    assert gates.label == "measured"
    # The numbers are the scorer's, unchanged, and the file they came from is named.
    published = json.loads(PUBLIC_RESULTS.read_text())
    assert (
        gates.mean_recall_at_evidence_limit
        == (published["retrieval"]["mean_recall_at_evidence_limit"])
    )
    assert (
        gates.fabricated_identifier_count
        == (published["citation_integrity"]["fabricated_identifier_count"])
    )
    assert gates.scored_by == "public-results.json"
    # Which file, without saying where anyone's copy of it lives.
    assert gates.scored_by_sha256 == sha256(PUBLIC_RESULTS.read_bytes()).hexdigest()

    # Every number in the artefact sits in a block that says how it was arrived at, and
    # in this step every one of those says "measured". Prediction arrives in S09.
    payload = scored.model_dump(mode="json")
    labels: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            block = cast("dict[str, object]", value)
            numbers = [
                key
                for key, item in block.items()
                if isinstance(item, int | float) and not isinstance(item, bool)
            ]
            if numbers:
                assert "label" in block, f"numbers with no label: {numbers}"
                labels.append(str(block["label"]))
            for item in block.values():
                walk(item)
        elif isinstance(value, list):
            for item in cast("list[object]", value):
                walk(item)

    walk(payload)
    assert labels, "the artefact carried no labelled numbers at all"
    assert set(labels) == {"measured"}


def test_a_question_list_and_the_frozen_benchmark_are_the_only_two_sources(
    tmp_path: Path,
) -> None:
    """No third set of questions, and no benchmark invented here."""

    source, asked = load_questions(BENCHMARK)
    assert "archiv-real-work-field-trial-v1" in source
    assert len(asked) >= 20

    with pytest.raises(CalibrationInputError, match="not both"):
        load_questions(BENCHMARK, question_file(tmp_path))

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(CalibrationInputError, match="non-empty"):
        load_questions(None, empty)


def test_a_question_no_model_answered_is_not_timed_as_if_one_had(tmp_path: Path) -> None:
    """A refused question takes almost no time. Averaging that in flatters the model.

    With no model configured, `ask` refuses before building a prompt and comes back in
    well under a millisecond. Reporting that as the median question time would say the
    model is a thousand times faster than it is, so it is not reported at all -- and the
    artefact says why rather than leaving the field out.
    """

    home = prepared_archive(tmp_path)
    save_model_config(ModelConfig(adapter="disabled"), home)

    calibration = run_calibration(home=home, questions=question_file(tmp_path))

    assert calibration.model.questions_where_a_model_ran == 0
    assert isinstance(calibration.model.wall_clock_ms, Unmeasured)
    assert "reached a model" in calibration.model.wall_clock_ms.reason
    assert all(not row.a_model_ran for row in calibration.workload.questions)

    # Retrieval is still measured: it happened, and it is the model-independent half.
    assert calibration.workload.retrieval_ms.count == 2


def test_an_interrupted_run_cannot_leave_a_half_written_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`runs/` is terminal, so a truncated file there stays there.

    Archiv's own integrity check reads every JSON file under `runs/`, and one that will
    not parse makes the whole archive refuse to be backed up. So the record goes to a
    temporary name and is moved into place, the same way every other run-evidence file
    in this project is written.
    """

    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))
    calibration = run_calibration(home=home, questions=question_file(tmp_path))

    directory = home / "runs" / "calibration" / calibration.calibration_id
    # The move leaves nothing behind, so nothing under runs/ is a partial file.
    assert [path.name for path in sorted(directory.iterdir())] == ["calibration.json"]
    json.loads((directory / "calibration.json").read_text())

    source = Path(run_calibration.__code__.co_filename).read_text(encoding="utf-8")
    assert "os.replace(temporary, destination)" in source


# --- S09: predicting local runtime, labelled estimated until measured ----------


def stub_local_server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prompt_tokens: int = 1400,
    prefill_per_second: float = 200.0,
    decode_per_second: float = 8.0,
) -> None:
    """A local server that takes as long as the speeds given, and reports its usage.

    Stands in for llama.cpp or Ollama. Nothing here opens a connection: what is being
    exercised is the arithmetic that separates prompt reading from generation, and a real
    server would only make the timing noisier.
    """

    import archiv.calibration as calibration_module

    class Probe:
        def probe(self, prompt: str, max_output_tokens: int) -> tuple[str, ReportedUsage]:
            del prompt
            produced = max_output_tokens
            seconds = prompt_tokens / prefill_per_second + produced / decode_per_second
            # Advance a clock rather than actually waiting: the test asserts the
            # arithmetic, and sleeping would only make it slow and flaky.
            fake_clock.advance(seconds)
            return "an answer", ReportedUsage(
                prompt_tokens=prompt_tokens, completion_tokens=produced
            )

    class Clock:
        def __init__(self) -> None:
            self.now = datetime(2026, 1, 1, tzinfo=UTC)

        def advance(self, seconds: float) -> None:
            self.now += timedelta(seconds=seconds)

    fake_clock = Clock()

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # pyright: ignore[reportIncompatibleMethodOverride]
            del tz
            return fake_clock.now

    def build_probe(config: object) -> Probe:
        del config
        return Probe()

    monkeypatch.setattr(calibration_module, "datetime", FrozenDatetime)
    monkeypatch.setattr("archiv.model_adapter.OpenAICompatibleLoopbackAdapter", build_probe)


def test_every_hardware_profile_row_cites_a_source_and_a_date() -> None:
    """A row without the page it came from is worthless, so the contract refuses one.

    This is the whole basis of the step: these are other people's measurements, and a
    figure nobody can go and check is indistinguishable from one somebody made up.
    """

    from archiv.hardware_profiles import HardwareProfile, ThroughputBand, load_published_profiles

    profiles = load_published_profiles(ROOT / "docs/plan/hardware-profiles.json")
    assert len(profiles) >= 8, "a table this thin cannot cover plausible hardware"

    for profile in profiles:
        assert profile.confidence == "estimated", (
            "no row in the committed table may claim to be measured: this project has "
            "never run on any of this hardware"
        )
        assert profile.source_url.startswith("https://")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", profile.retrieved_on), profile.id
        assert profile.harness.strip(), profile.id
        # And the spread is described, so one observation cannot pose as a range.
        assert profile.prefill_tokens_per_second.basis.strip()
        assert profile.decode_tokens_per_second.basis.strip()

    # The contract itself refuses a row with no source, rather than trusting the table.
    band = ThroughputBand(low=1.0, high=2.0, basis="made up for this test")
    with pytest.raises(ValueError, match="the page it came from"):
        HardwareProfile(
            id="nowhere",
            model="a model",
            parameter_count_billions=7.0,
            quantisation="Q4_0",
            hardware="somebody's computer",
            backend="something",
            prefill_tokens_per_second=band,
            decode_tokens_per_second=band,
            harness="unstated",
            confidence="estimated",
        )

    # And a measured row with no machine is refused for the mirror-image reason.
    with pytest.raises(ValueError, match="the machine it was measured on"):
        HardwareProfile(
            id="somewhere",
            model="a model",
            parameter_count_billions=7.0,
            quantisation="Q4_0",
            hardware="somebody's computer",
            backend="something",
            prefill_tokens_per_second=band,
            decode_tokens_per_second=band,
            harness="two probes",
            confidence="measured",
        )


def test_predictions_from_published_figures_are_labelled_estimated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A number nobody measured must never be able to pass as one somebody did."""

    from archiv.hardware_profiles import ProfileError, load_published_profiles, predict_ask_latency

    profiles = {
        row.id: row for row in load_published_profiles(ROOT / "docs/plan/hardware-profiles.json")
    }
    prediction = predict_ask_latency(
        profiles["nvidia-rtx-4090-llama2-7b-q4-0"],
        prompt_tokens=1400,
        completion_tokens=300,
        retrieval_ms=120.0,
    )
    assert prediction.label == "estimated"
    assert prediction.band_fraction == pytest.approx(0.40)
    assert prediction.source_url.startswith("https://")
    assert prediction.retrieved_on
    assert prediction.measured_on_machine == ""
    # The two halves of the wait are reported apart, because they have different causes.
    assert prediction.time_to_first_token.middle_ms != prediction.generation.middle_ms
    assert prediction.total.low_ms < prediction.total.middle_ms < prediction.total.high_ms

    # Asking about hardware there is no figure for gets a refusal, not a guess.
    home = prepared_archive(tmp_path)
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))
    calibration = run_calibration(home=home, questions=question_file(tmp_path))
    with pytest.raises(ProfileError, match="will not guess"):
        predict_from_calibration(
            calibration,
            "some-machine-nobody-measured",
            home=home,
            published=ROOT / "docs/plan/hardware-profiles.json",
        )


def test_calibrate_local_overwrites_a_row_as_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the hardware exists, the estimate is replaced by a fact about that machine.

    And the fact lives in the archive, never in the committed table: a figure measured on
    somebody's laptop is about their laptop, and publishing it would also mean the table's
    rows no longer all came from a page anyone can check.
    """

    from archiv.hardware_profiles import (
        available_profiles,
        load_published_profiles,
        measured_profiles_path,
        save_measured_profile,
    )

    home = prepared_archive(tmp_path)
    stub_local_server(
        monkeypatch, prompt_tokens=1400, prefill_per_second=200.0, decode_per_second=8.0
    )

    measured = measure_local_throughput(home=home, prompt="a prompt of some length")
    # Recovered from two replies of different lengths, to within rounding.
    assert measured.decode_tokens_per_second == pytest.approx(8.0, rel=0.02)
    assert measured.prefill_tokens_per_second == pytest.approx(200.0, rel=0.02)
    assert measured.machine

    saved = save_measured_profile(local_profile_from(measured), home)
    assert saved == measured_profiles_path(home)
    assert str(home) in str(saved), "a machine's own figures stay inside its archive"

    rows = available_profiles(home, ROOT / "docs/plan/hardware-profiles.json")
    this_machine = next(row for row in rows if row.id == "this-machine")
    assert this_machine.confidence == "measured"
    assert this_machine.measured_on_machine == measured.machine
    # The published rows are still there, and still estimated.
    published = load_published_profiles(ROOT / "docs/plan/hardware-profiles.json")
    assert len(rows) == len(published) + 1
    assert all(row.confidence == "estimated" for row in rows if row.id != "this-machine")

    # A prediction from it is labelled measured and names the machine.
    install_stub(monkeypatch, ReportedUsage(prompt_tokens=900, completion_tokens=120))
    calibration = run_calibration(home=home, questions=question_file(tmp_path))
    prediction = predict_from_calibration(
        calibration, "this-machine", home=home, published=ROOT / "docs/plan/hardware-profiles.json"
    )
    assert prediction.label == "measured"
    assert prediction.measured_on_machine == measured.machine


def test_local_measurement_refuses_rather_than_reporting_a_remote_model_as_this_machine(
    tmp_path: Path,
) -> None:
    """A paid model on somebody else's computer is not this machine's speed."""

    from archiv.model_adapter import ModelConfig, save_model_config

    home = prepared_archive(tmp_path)
    save_model_config(
        ModelConfig(
            adapter="remote-evaluation",
            endpoint="https://api.openai.com",
            model="a-model",
            api_key_env="ARCHIV_TEST_S09_KEY",
        ),
        home,
    )
    with pytest.raises(LocalMeasurementRefused, match="not this machine's speed"):
        measure_local_throughput(home=home, prompt="a prompt")


def test_prediction_lands_within_the_stated_band_for_a_known_fingerprint() -> None:
    """Fixed workload, fixed throughput: this checks the arithmetic, not the world."""

    from archiv.hardware_profiles import (
        HardwareProfile,
        ThroughputBand,
        bandwidth_anchor,
        predict_ask_latency,
    )

    # 1000 prompt tokens at 100 a second is 10 s; 200 reply tokens at 10 a second is 20 s.
    profile = HardwareProfile(
        id="fixed-for-arithmetic",
        model="a model",
        parameter_count_billions=8.0,
        quantisation="Q4_0",
        hardware="a machine chosen to make the sums obvious",
        backend="none",
        prefill_tokens_per_second=ThroughputBand(low=100.0, high=100.0, basis="fixed"),
        decode_tokens_per_second=ThroughputBand(low=10.0, high=10.0, basis="fixed"),
        harness="fixed",
        confidence="measured",
        measured_on_machine="a machine chosen to make the sums obvious",
    )
    prediction = predict_ask_latency(
        profile, prompt_tokens=1000, completion_tokens=200, retrieval_ms=500.0
    )

    assert prediction.time_to_first_token.middle_ms == pytest.approx(10_500.0)
    assert prediction.generation.middle_ms == pytest.approx(20_000.0)
    assert prediction.total.middle_ms == pytest.approx(30_500.0)
    # The band is the stated fraction either side, not something wider or narrower.
    assert prediction.total.low_ms == pytest.approx(30_500.0 * 0.6)
    assert prediction.total.high_ms == pytest.approx(30_500.0 * 1.4)
    assert prediction.total.low_ms <= prediction.total.middle_ms <= prediction.total.high_ms

    # With no bandwidth figure there is one anchor, and it says so rather than pretending.
    assert isinstance(prediction.second_anchor, HardwareUnavailable)
    assert prediction.anchors_disagree_by is None

    # Given one, the second anchor is derived and a large disagreement is reported as
    # the uncertainty rather than averaged into a single confident number.
    with_bandwidth = profile.model_copy(
        update={
            "memory_bandwidth_bytes_per_second": 100e9,
            "model_file_bytes": 5e9,
        }
    )
    anchor = bandwidth_anchor(with_bandwidth)
    assert not isinstance(anchor, HardwareUnavailable)
    # 100 GB/s over a 5 GB file is 20 reads a second; at 70% that is 14 tokens a second.
    assert anchor.decode_tokens_per_second_middle == pytest.approx(14.0)
    assert anchor.utilisation_assumed == pytest.approx(0.70)

    second = predict_ask_latency(
        with_bandwidth, prompt_tokens=1000, completion_tokens=200, retrieval_ms=500.0
    )
    # Published 10 a second against a derived 14 is a 40% gap measured against the figure
    # being checked, so it is over the threshold and reported rather than averaged away.
    # Measured instead against the larger of the two it would read as 29% and stay quiet;
    # that choice was made deliberately, because a threshold whose job is to surface
    # disagreement should not be the version that surfaces less of it.
    assert second.anchors_disagree_by == pytest.approx(0.4)
    assert "differ by 40%" in second.anchor_disagreement_note
    assert "reported rather than averaged away" in second.anchor_disagreement_note


def test_which_half_of_the_wait_dominates_depends_on_the_machine() -> None:
    """Neither the step's premise nor its first correction survives the figures.

    `docs/plan/steps/S09.md` first said prompt reading dominates on a machine without a
    graphics card. A correction written during the step said the opposite -- generation
    dominates on every row -- and that was wrong too, because the processor figures had
    been taken from a fork's column of a source publishing two side by side.

    On the mainline figures it goes both ways, and the deciding quantity is the ratio
    between the two rates. This pins that, including the one row where prompt reading
    genuinely wins, so neither of the two over-simple claims can come back.
    """

    from archiv.hardware_profiles import load_published_profiles

    profiles = {
        row.id: row for row in load_published_profiles(ROOT / "docs/plan/hardware-profiles.json")
    }
    prompt_tokens, answer_tokens = 1400, 300

    def halves(row_id: str) -> tuple[float, float]:
        row = profiles[row_id]
        return (
            prompt_tokens / row.prefill_tokens_per_second.middle,
            answer_tokens / row.decode_tokens_per_second.middle,
        )

    # Apple silicon on the processor alone: reading the prompt is the larger half, which
    # is what the step originally claimed and what its first correction denied.
    apple_prompt, apple_answer = halves("apple-m2-max-cpu-only-llama31-8b-q8-0")
    assert apple_prompt > apple_answer, (
        "on this row prompt reading dominates; a claim that generation always dominates "
        "is contradicted here"
    )

    # An x86 processor, and a graphics card: generation is the larger half, which is what
    # the step's original premise denied.
    for row_id in ("amd-ryzen-7950x-cpu-only-llama31-8b-q8-0", "nvidia-rtx-4090-llama2-7b-q4-0"):
        row_prompt, row_answer = halves(row_id)
        assert row_answer > row_prompt, (
            f"on {row_id} generation dominates; a claim that the wait is almost all "
            "before the first word is contradicted here"
        )

    # So no single claim covers the table, and the spread in the deciding ratio is what
    # makes that so rather than it being a coincidence of two rows.
    ratios = [
        row.prefill_tokens_per_second.middle / row.decode_tokens_per_second.middle
        for row in profiles.values()
    ]
    assert max(ratios) / min(ratios) > 10, (
        "the ratio that decides which half dominates barely varies, which would make a "
        "single claim about all machines defensible after all"
    )
    # And the answer length where the two halves are equal spans an order of magnitude.
    crossovers = sorted(prompt_tokens / ratio for ratio in ratios)
    assert crossovers[0] < 30
    assert crossovers[-1] > 300
