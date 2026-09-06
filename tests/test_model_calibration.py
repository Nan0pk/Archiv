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
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from archiv.calibration import (
    CalibrationInputError,
    Distribution,
    Unmeasured,
    load_questions,
    run_calibration,
)
from archiv.cli import app
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
    assert isinstance(model.completion_tokens_per_second_including_prompt_processing, Distribution)

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
    calibration = run_calibration(home=home, questions=question_file(tmp_path))

    payload = calibration.model_dump(mode="json")
    assert redact_private(payload, [str(home)]) == payload

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
    assert gates.scored_by.endswith("public-results.json")

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
