"""Measure what one grounded question actually costs, and how the model answers it.

Two kinds of number live here and they are not interchangeable.

**Time can be predicted later. Quality can only ever be measured.** The workload is
bounded, so how long a question takes extrapolates to other hardware -- that is step S09's
job, and everything it predicts from is recorded here as a measurement. How *well* a model
answers does not extrapolate. Published capability scores correlate weakly with grounding
ability: two models three points apart on a general benchmark were nearly thirty points
apart on grounding, and citation behaviour is something a model is trained to do rather
than a by-product of being good. So nothing here turns a measurement of one model into a
statement about another, and nothing here invents a scaling factor.

What follows from that is the shape of this file. Every number is labelled `measured`.
Anything that could not be measured is written down as a refusal to measure it, with the
reason -- never omitted, never filled in with a plausible figure. The most important
example is time to first token: this adapter does not stream, so prompt processing and
generation cannot be told apart, and both rates say so rather than guessing.

Nothing here is a benchmark of its own. The questions come from the frozen benchmark the
field trial already uses, and the quality figures are copied from that harness's scorer
rather than re-derived, so there is one definition of a good answer in this project.
"""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import Field

from archiv.contracts import RunStatus, StrictModel
from archiv.cost_control import SpendLedgerUnreadableError, load_ledger
from archiv.grounding import run_grounded_ask
from archiv.model_adapter import describe_model, load_model_config
from archiv.search import retrieve_evidence
from archiv.storage.layout import ArchivLayout

Label = Literal["measured", "estimated"]

DEFAULT_BENCHMARK = Path("benchmarks/field_trial/benchmark.json")


class CalibrationInputError(ValueError):
    """The questions or the scored results handed to a calibration run are unusable."""


class Unmeasured(StrictModel):
    """A number that was not produced, and why.

    Written in place of the number rather than left out. An absent field reads as an
    oversight; this reads as a fact about the evidence, which is what it is.
    """

    status: Literal["not_measurable", "not_measured", "externally_blocked"]
    reason: str = Field(min_length=1)


class Distribution(StrictModel):
    """A set of measurements, described without keeping the individual values twice."""

    label: Label = "measured"
    count: int = Field(ge=1)
    minimum: float
    median: float
    maximum: float
    total: float


class QuestionWorkload(StrictModel):
    """One question's shape. Its identifier only -- never a word of what it asked."""

    label: Label = "measured"
    question_id: str = Field(min_length=1)
    retrieval_ms: float
    selected_passage_count: int
    candidate_passage_count: int
    ask_wall_clock_ms: float
    a_model_ran: bool
    run_status: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    spend_usd: float | None = None


class WorkloadFingerprint(StrictModel):
    """What a question costs in work, whatever model answers it.

    This is the part that stays true when the model changes, and it is what a later
    prediction of local running time is built on.
    """

    label: Label = "measured"
    evidence_limit: int
    question_count: int
    retrieval_ms: Distribution
    prompt_tokens: Distribution | Unmeasured
    completion_tokens: Distribution | Unmeasured
    prompt_token_source: str
    questions: list[QuestionWorkload]


class MeasuredModel(StrictModel):
    """One model, measured on this workload. Never a forecast about a different one."""

    label: Label = "measured"
    adapter: str
    provenance: str
    identity: str
    questions_asked: int
    questions_answered: int
    questions_not_answered: int
    questions_where_a_model_ran: int
    """The denominator for everything below. A question a model never saw -- refused by
    policy, or with no evidence to answer from -- took almost no time, and averaging that
    in would report the model as faster than it is."""
    wall_clock_ms: Distribution | Unmeasured
    completion_tokens_per_second_including_prompt_processing: Distribution | Unmeasured
    time_to_first_token_ms: Unmeasured
    prefill_tokens_per_second: Unmeasured
    decode_tokens_per_second: Unmeasured


class QualityGates(StrictModel):
    """Copied from the field trial's scorer, not worked out again here.

    There is one definition in this project of whether an answer was any good, and it
    lives in the field-trial scoring code. A second definition computed here would
    disagree with it eventually, and then two numbers would both claim to be the quality.
    """

    label: Label = "measured"
    scored_by: str = Field(min_length=1)
    benchmark_id: str
    question_count: int
    mean_recall_at_evidence_limit: float
    questions_with_retrieval_miss: int
    structurally_valid_questions: int
    fabricated_identifier_count: int
    honesty_checks_passed: int
    unsupported_claim_count: int


class SpendSummary(StrictModel):
    """What this calibration run cost, from the provider's own reported usage."""

    label: Label = "measured"
    spent_before_usd: float
    spent_after_usd: float
    spent_during_calibration_usd: float
    recorded_calls: int
    unmeasured_calls: int


class MeasurementSite(StrictModel):
    """Where this ran, in terms that identify the machine's kind and not the machine."""

    label: Label = "measured"
    platform: str
    machine: str
    python_version: str
    continuous_integration: bool


class Calibration(StrictModel):
    """One archive, one model, one workload, measured on one day."""

    schema_version: str = "1"
    calibration_id: str
    measured_on: str
    measured_head_sha: str | Unmeasured
    measured_where: MeasurementSite
    question_source: str
    workload: WorkloadFingerprint
    model: MeasuredModel
    quality: QualityGates | Unmeasured
    spend: SpendSummary | Unmeasured
    known_limitations: list[str]


def _distribution(values: Sequence[float]) -> Distribution | Unmeasured:
    if not values:
        return Unmeasured(
            status="not_measured",
            reason="no question produced this measurement on this run",
        )
    ordered = sorted(values)
    return Distribution(
        count=len(ordered),
        minimum=round(ordered[0], 4),
        median=round(ordered[len(ordered) // 2], 4),
        maximum=round(ordered[-1], 4),
        total=round(sum(ordered), 4),
    )


def _required_distribution(values: Sequence[float]) -> Distribution:
    measured = _distribution(values)
    if isinstance(measured, Unmeasured):  # pragma: no cover - callers pass a non-empty list
        raise CalibrationInputError("a calibration run needs at least one question")
    return measured


def _head_sha() -> str | Unmeasured:
    """The commit this was measured at, when the code is running from a checkout.

    A number with no commit behind it cannot be compared with the next one, so this is
    part of the artefact rather than something a reader is expected to remember. When
    Archiv is running from an installed copy there is no commit, and that is recorded as
    what it is rather than left blank.
    """

    try:
        finished = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return Unmeasured(
            status="not_measured",
            reason=f"git could not be run to read the commit: {type(error).__name__}: {error}",
        )
    revision = finished.stdout.strip()
    if finished.returncode != 0 or len(revision) != 40:
        return Unmeasured(
            status="not_measured",
            reason=(
                "this Archiv is not running from a git checkout, so there is no commit "
                "to record against these numbers"
            ),
        )
    return revision


def _site() -> MeasurementSite:
    import os

    return MeasurementSite(
        platform=platform.system(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        continuous_integration=bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
    )


def load_questions(
    benchmark: Path | None = None, questions: Path | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Where the questions come from, and what they are: identifier and text.

    Either the frozen benchmark the field trial already uses, or a list supplied for a
    private archive. Not a new benchmark: this project has one, and a second set of
    questions scored against a second definition of a good answer would be worse than
    no calibration at all.
    """

    if benchmark is not None and questions is not None:
        raise CalibrationInputError("give either a benchmark or a question list, not both")
    if questions is not None:
        try:
            supplied = json.loads(questions.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CalibrationInputError(f"cannot read the question list: {error}") from error
        if not isinstance(supplied, list) or not supplied:
            raise CalibrationInputError("the question list must be a non-empty JSON list")
        texts = cast("list[object]", supplied)
        if not all(isinstance(item, str) and item.strip() for item in texts):
            raise CalibrationInputError("every question must be a non-empty string")
        return (
            "a supplied question list",
            [(f"Q{index:03d}", str(text)) for index, text in enumerate(texts, start=1)],
        )

    path = benchmark or DEFAULT_BENCHMARK
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationInputError(f"cannot read the benchmark: {error}") from error
    if not isinstance(loaded, dict):
        raise CalibrationInputError("the benchmark must be a schema_version 1 object")
    payload = cast("dict[str, object]", loaded)
    if payload.get("schema_version") != "1":
        raise CalibrationInputError("the benchmark must be a schema_version 1 object")
    rows = payload.get("questions")
    if not isinstance(rows, list) or not rows:
        raise CalibrationInputError("the benchmark has no questions")
    collected: list[tuple[str, str]] = []
    for row in cast("list[object]", rows):
        if not isinstance(row, dict):
            raise CalibrationInputError("benchmark questions must be objects")
        entry = cast("dict[str, object]", row)
        question_id, text = entry.get("id"), entry.get("question")
        if not isinstance(question_id, str) or not isinstance(text, str) or not text.strip():
            raise CalibrationInputError("every benchmark question needs an id and text")
        collected.append((question_id, text))
    identifier = payload.get("benchmark_id")
    name = identifier if isinstance(identifier, str) else path.name
    return f"the frozen benchmark {name}", collected


def load_quality_gates(results: Path) -> QualityGates:
    """Lift the scorer's own numbers out of a field-trial results file."""

    try:
        loaded = json.loads(results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationInputError(f"cannot read the scored results: {error}") from error
    if not isinstance(loaded, dict):
        raise CalibrationInputError("scored results must be a JSON object")
    payload = cast("dict[str, object]", loaded)
    # Both shapes the harness writes: the committed public summary, which is already
    # flattened, and a fresh run's output, which keeps the sections under "aggregate".
    aggregate = payload.get("aggregate")
    sections = cast("dict[str, object]", aggregate) if isinstance(aggregate, dict) else payload

    def section(name: str) -> dict[str, object]:
        value = sections.get(name)
        if not isinstance(value, dict):
            raise CalibrationInputError(f"scored results have no {name} section")
        return cast("dict[str, object]", value)

    retrieval = section("retrieval")
    citations = section("citation_integrity")
    answers = section("answer_quality")
    return QualityGates(
        scored_by=str(results),
        benchmark_id=str(payload.get("benchmark_id", "unknown")),
        question_count=int(cast(int, payload.get("question_count", 0))),
        mean_recall_at_evidence_limit=float(
            cast(float, retrieval["mean_recall_at_evidence_limit"])
        ),
        questions_with_retrieval_miss=int(cast(int, retrieval["questions_with_retrieval_miss"])),
        structurally_valid_questions=int(cast(int, citations["structurally_valid_questions"])),
        fabricated_identifier_count=int(cast(int, citations["fabricated_identifier_count"])),
        honesty_checks_passed=int(cast(int, answers["honesty_checks_passed"])),
        unsupported_claim_count=int(cast(int, answers["unsupported_claim_count"])),
    )


def _usage_of(evidence_dir: Path) -> tuple[int | None, int | None]:
    """Token counts the model reported for a run, summed over its attempts.

    A retry was paid for and used tokens too, so all of them count. If any attempt
    reported nothing, the total is unknown rather than partial: a sum missing one of its
    terms is not a measurement.
    """

    path = evidence_dir / "usage.json"
    if not path.is_file():
        return None, None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(loaded, dict):
        return None, None
    attempts = cast("dict[str, object]", loaded).get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None, None
    prompt_total = 0
    completion_total = 0
    for attempt in cast("list[object]", attempts):
        if not isinstance(attempt, dict):
            return None, None
        row = cast("dict[str, object]", attempt)
        prompt_used, completion_used = row.get("prompt_tokens"), row.get("completion_tokens")
        if not isinstance(prompt_used, int) or not isinstance(completion_used, int):
            return None, None
        prompt_total += prompt_used
        completion_total += completion_used
    return prompt_total, completion_total


def _counted_prompt_tokens(evidence_dir: Path) -> int | None:
    """The pre-flight count, used only when the provider reported nothing itself."""

    path = evidence_dir / "cost.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    preflight = cast("dict[str, object]", loaded).get("preflight")
    if not isinstance(preflight, dict):
        return None
    counted = cast("dict[str, object]", preflight).get("prompt_tokens")
    return counted if isinstance(counted, int) else None


def _ledger_or_none(home: Path) -> tuple[float, int, int] | None:
    try:
        ledger = load_ledger(home)
    except SpendLedgerUnreadableError:
        return None
    return ledger.spent_usd, ledger.recorded_calls, ledger.unmeasured_calls


def run_calibration(
    *,
    home: Path | None = None,
    benchmark: Path | None = None,
    questions: Path | None = None,
    quality_from: Path | None = None,
    evidence_limit: int = 8,
) -> Calibration:
    """Ask a fixed set of questions, and write down what each one actually took."""

    layout = ArchivLayout.resolve(home)
    layout.ensure()
    source, asked = load_questions(benchmark, questions)
    if not asked:
        raise CalibrationInputError("a calibration run needs at least one question")
    config = load_model_config(layout.root)
    identity, _ = describe_model(config)

    before = _ledger_or_none(layout.root)
    rows: list[QuestionWorkload] = []
    retrieval_times: list[float] = []
    wall_clocks: list[float] = []
    prompt_counts: list[float] = []
    completion_counts: list[float] = []
    throughputs: list[float] = []
    reported_prompt_tokens = False
    # What the ledger said before this question, so each question's cost is the change
    # it caused rather than a share of a total.
    running_total = before[0] if before is not None else None

    for question_id, text in asked:
        started = datetime.now(UTC)
        try:
            package = retrieve_evidence(text, home=layout.root, evidence_limit=evidence_limit)
        except FileNotFoundError as error:
            # An empty archive has no index to search. Timing a search that cannot happen
            # would produce a number, and the number would mean nothing.
            raise CalibrationInputError(
                "this archive has nothing indexed yet, so there is no work to measure. "
                "Add documents with 'archiv add <path>' and try again."
            ) from error
        retrieval_ms = (datetime.now(UTC) - started).total_seconds() * 1000

        asked_at = datetime.now(UTC)
        result = run_grounded_ask(text, home=layout.root, max_sources=evidence_limit)
        wall_clock_ms = (datetime.now(UTC) - asked_at).total_seconds() * 1000

        evidence_dir = Path(result.evidence_dir)
        prompt_tokens, completion_tokens = _usage_of(evidence_dir)
        if prompt_tokens is not None:
            reported_prompt_tokens = True
        else:
            prompt_tokens = _counted_prompt_tokens(evidence_dir)

        during = _ledger_or_none(layout.root)
        spend_usd: float | None = None
        if running_total is not None and during is not None:
            spend_usd = round(during[0] - running_total, 6)
            running_total = during[0]

        # Under-counts rather than over-claims: a call that failed before reporting
        # anything is not counted as having run, which makes the model look no faster
        # than it is.
        a_model_ran = result.raw_model_response is not None or completion_tokens is not None

        retrieval_times.append(retrieval_ms)
        if a_model_ran:
            wall_clocks.append(wall_clock_ms)
        if prompt_tokens is not None:
            prompt_counts.append(float(prompt_tokens))
        if completion_tokens is not None:
            completion_counts.append(float(completion_tokens))
            if wall_clock_ms > 0 and a_model_ran:
                throughputs.append(completion_tokens / (wall_clock_ms / 1000))

        rows.append(
            QuestionWorkload(
                question_id=question_id,
                retrieval_ms=round(retrieval_ms, 4),
                selected_passage_count=package.diagnostics.selected_count,
                candidate_passage_count=package.diagnostics.candidate_count,
                ask_wall_clock_ms=round(wall_clock_ms, 4),
                a_model_ran=a_model_ran,
                run_status=result.status.value,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                spend_usd=spend_usd,
            )
        )

    answered = sum(1 for row in rows if row.run_status == RunStatus.SUCCEEDED.value)
    after = _ledger_or_none(layout.root)

    prompt_source = (
        "the provider's own reported usage"
        if reported_prompt_tokens
        else "the pre-flight count from the pinned tokenizer, where one was configured"
    )
    no_stream = (
        "this adapter asks for a whole reply rather than a stream, so there is no first "
        "token to time"
    )

    calibration = Calibration(
        calibration_id=uuid4().hex,
        measured_on=datetime.now(UTC).isoformat(timespec="seconds"),
        measured_head_sha=_head_sha(),
        measured_where=_site(),
        question_source=source,
        workload=WorkloadFingerprint(
            evidence_limit=evidence_limit,
            question_count=len(rows),
            retrieval_ms=_required_distribution(retrieval_times),
            prompt_tokens=_distribution(prompt_counts)
            if prompt_counts
            else Unmeasured(
                status="not_measured",
                reason=(
                    "no model reported its usage and no pinned tokenizer is configured, "
                    "so the prompt was never counted; 'archiv model spend status' says "
                    "which of those applies here"
                ),
            ),
            completion_tokens=_distribution(completion_counts)
            if completion_counts
            else Unmeasured(
                status="not_measured",
                reason="no model reported how many tokens it produced on this run",
            ),
            prompt_token_source=prompt_source,
            questions=rows,
        ),
        model=MeasuredModel(
            adapter=config.adapter,
            provenance=config.provenance,
            identity=identity,
            questions_asked=len(rows),
            questions_answered=answered,
            questions_not_answered=len(rows) - answered,
            questions_where_a_model_ran=len(wall_clocks),
            wall_clock_ms=_distribution(wall_clocks)
            if wall_clocks
            else Unmeasured(
                status="not_measured",
                reason=(
                    "no question on this run reached a model, so there is no model time "
                    "to report. What the archive is configured to use is recorded above"
                ),
            ),
            completion_tokens_per_second_including_prompt_processing=(
                _distribution(throughputs)
                if throughputs
                else Unmeasured(
                    status="not_measured",
                    reason=(
                        "no model reported how many tokens it produced, so a rate would "
                        "be a division by a number nobody measured"
                    ),
                )
            ),
            time_to_first_token_ms=Unmeasured(status="not_measurable", reason=no_stream),
            prefill_tokens_per_second=Unmeasured(
                status="not_measurable",
                reason=(
                    "telling prompt processing apart from generation needs a time to "
                    f"first token, and {no_stream}"
                ),
            ),
            decode_tokens_per_second=Unmeasured(
                status="not_measurable",
                reason=(
                    "telling generation apart from prompt processing needs a time to "
                    f"first token, and {no_stream}. The rate that could be measured "
                    "without one is recorded beside this, and it includes prompt "
                    "processing"
                ),
            ),
        ),
        quality=(
            load_quality_gates(quality_from)
            if quality_from is not None
            else Unmeasured(
                status="not_measured",
                reason=(
                    "answer quality is scored by the field trial, not here. Run it and "
                    "pass its results file to record the scores against these timings"
                ),
            )
        ),
        spend=(
            SpendSummary(
                spent_before_usd=before[0],
                spent_after_usd=after[0],
                spent_during_calibration_usd=round(after[0] - before[0], 6),
                recorded_calls=after[1] - before[1],
                unmeasured_calls=after[2] - before[2],
            )
            if before is not None and after is not None
            else Unmeasured(
                status="not_measured",
                reason=(
                    "this archive's record of what it has spent could not be read, so "
                    "no cost can be attributed to this run"
                ),
            )
        ),
        known_limitations=[
            "These numbers describe one model on one machine. Nothing here predicts how "
            "a different model would answer: grounding and citation behaviour are "
            "learned, and do not follow from a model's general capability score.",
            "Everything recorded here is measured. Predicting how long this workload "
            "would take on other hardware is a later step, and its numbers will say so.",
            no_stream.capitalize() + ", so prompt processing and generation cannot be "
            "separated on this adapter.",
        ],
    )

    output_dir = layout.runs / "calibration" / calibration.calibration_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "calibration.json").write_text(
        json.dumps(calibration.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return calibration


def calibration_path(home: Path | None, calibration_id: str) -> Path:
    return ArchivLayout.resolve(home).runs / "calibration" / calibration_id / "calibration.json"
