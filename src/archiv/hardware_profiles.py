"""Predict how slow a grounded question would be on hardware nobody here has.

This is the one place in Archiv allowed to report a number that was not measured, and
everything about it is built to keep that number from being mistaken for a measurement.

The prediction splits in two because the machine does. Reading the prompt happens all at
once and is limited by how fast the processor can compute; producing the reply happens one
token at a time and is limited by how fast the weights can be read out of memory. Archiv's
questions are prompt-heavy -- about eight passages of evidence plus an instruction block,
against a few hundred tokens of answer -- so on a machine without a graphics card almost
the whole wait happens before the first word appears. A single "tokens per second" figure
hides that entirely, which is the mistake this module exists to avoid.

Three rules, and they are not negotiable:

- An estimated figure never appears without its band and the page it came from.
- A measured figure never appears without the machine it was measured on.
- If no profile matches the hardware asked about, there is no prediction. Saying nothing
  is a better answer than a made-up number, and the project already applies that rule to
  a blocked benchmark cell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, model_validator

from archiv.contracts import StrictModel
from archiv.storage.layout import ArchivLayout

PUBLISHED_PROFILES = Path("docs/plan/hardware-profiles.json")

PREDICTION_BAND_FRACTION = 0.40
"""Every single prediction is quoted plus or minus this much.

Not a hedge and not physics. Measurements of the same model on the same hardware differ
by 1.4 to 1.7 times between inference engines, and quantisation variants move it again --
the row for a three-bit build in the published table is slower than the eight-bit build
on the same processor, which is the opposite of the usual expectation. The underlying
behaviour is well enough understood; what is uncertain is which of several
implementations somebody will actually install.
"""

CONSUMER_MEMORY_BANDWIDTH_UTILISATION = 0.70
"""The share of a machine's peak memory bandwidth that single-stream generation achieves.

Named here rather than buried in an expression, so it can be argued with. Two things
about it are worth knowing and are easy to get backwards: it *falls* as bandwidth rises,
because the fixed cost per token does not shrink with a faster memory bus, and it *rises*
with the size of the weights, because a bigger read amortises that fixed cost better.
"""

CONSUMER_MEMORY_BANDWIDTH_UTILISATION_BAND = (0.55, 0.85)
"""The range the figure above sits in for ordinary desktop and laptop hardware."""

ANCHOR_DISAGREEMENT_THRESHOLD = 0.30
"""Past this much, two independent estimates disagreeing *is* the answer.

Averaging them would produce one confident number out of two that contradict each other,
which is worse than either alone because it hides that anything was in dispute.
"""


class ProfileError(ValueError):
    """The profile table, or a request against it, is unusable."""


class Unavailable(StrictModel):
    """Something that could not be worked out, and why. Never an omission."""

    status: Literal["no_matching_profile", "not_derivable", "not_measured"]
    reason: str = Field(min_length=1)


class ThroughputBand(StrictModel):
    """A published throughput figure and the spread its own source reported."""

    low: float = Field(gt=0)
    high: float = Field(gt=0)
    basis: str = Field(min_length=1)
    """Where the spread came from -- or that there is none, and this is one observation."""

    @model_validator(mode="after")
    def _low_is_not_above_high(self) -> ThroughputBand:
        if self.low > self.high:
            raise ValueError("the low end of a throughput band cannot exceed the high end")
        return self

    @property
    def middle(self) -> float:
        return (self.low + self.high) / 2


class HardwareProfile(StrictModel):
    """One combination of model, quantisation and machine, with throughput figures."""

    id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parameter_count_billions: float = Field(gt=0)
    quantisation: str = Field(min_length=1)
    hardware: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    prefill_tokens_per_second: ThroughputBand
    decode_tokens_per_second: ThroughputBand
    harness: str = Field(min_length=1)
    confidence: Literal["estimated", "measured"]
    source_url: str = ""
    """The page the figure was read off. Required for an estimated row."""
    retrieved_on: str = ""
    measured_on_machine: str = ""
    """The machine the figure was measured on. Required for a measured row."""
    memory_bandwidth_bytes_per_second: float | None = None
    model_file_bytes: float | None = None
    note: str = ""

    @model_validator(mode="after")
    def _a_figure_carries_where_it_came_from(self) -> HardwareProfile:
        if self.confidence == "estimated":
            if not self.source_url.strip() or not self.retrieved_on.strip():
                raise ValueError(
                    f"profile {self.id}: an estimated figure needs the page it came from "
                    "and the day it was read; a row without those is not evidence"
                )
        elif not self.measured_on_machine.strip():
            raise ValueError(
                f"profile {self.id}: a measured figure needs the machine it was measured "
                "on, or it is a number about nothing in particular"
            )
        return self


class ProfileTable(StrictModel):
    """The published table. Extra keys are prose for a human reader, not data."""

    schema_version: str
    profiles: list[HardwareProfile] = Field(min_length=1)
    purpose: str = ""
    how_to_read_this: list[str] = Field(default_factory=list[str])
    what_this_table_does_not_contain: list[str] = Field(default_factory=list[str])


class Band(StrictModel):
    """A predicted duration, with the honest spread around it."""

    low_ms: float
    middle_ms: float
    high_ms: float


class BandwidthAnchor(StrictModel):
    """A second, independent estimate of generation speed, from memory bandwidth.

    Derived rather than published: how fast the weights can be read, times the share of
    peak bandwidth actually achieved. The size used is the model file's real size on disk,
    not parameter count times nominal bits, which understates it.
    """

    decode_tokens_per_second_low: float
    decode_tokens_per_second_middle: float
    decode_tokens_per_second_high: float
    utilisation_assumed: float
    utilisation_band: tuple[float, float]
    model_file_bytes: float
    memory_bandwidth_bytes_per_second: float


class PredictedLatency(StrictModel):
    """What one grounded question is expected to cost on one machine."""

    label: Literal["estimated", "measured"]
    band_fraction: float = PREDICTION_BAND_FRACTION
    profile_id: str
    hardware: str
    model: str
    quantisation: str
    source_url: str = ""
    retrieved_on: str = ""
    measured_on_machine: str = ""
    prompt_tokens: int
    completion_tokens: int
    retrieval_ms: float
    time_to_first_token: Band
    """Reading the prompt. On a machine without a graphics card this is most of the wait."""
    generation: Band
    total: Band
    second_anchor: BandwidthAnchor | Unavailable
    anchors_disagree_by: float | None = None
    anchor_disagreement_note: str = ""

    @model_validator(mode="after")
    def _a_prediction_carries_where_it_came_from(self) -> PredictedLatency:
        if self.label == "estimated" and not self.source_url.strip():
            raise ValueError("an estimated prediction must name the page its figures came from")
        if self.label == "measured" and not self.measured_on_machine.strip():
            raise ValueError("a measured prediction must name the machine it was measured on")
        return self


def _band(middle_ms: float) -> Band:
    return Band(
        low_ms=round(middle_ms * (1 - PREDICTION_BAND_FRACTION), 3),
        middle_ms=round(middle_ms, 3),
        high_ms=round(middle_ms * (1 + PREDICTION_BAND_FRACTION), 3),
    )


def load_published_profiles(path: Path | None = None) -> list[HardwareProfile]:
    """Read the committed table of published figures."""

    source = path or PUBLISHED_PROFILES
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"cannot read the hardware profile table: {error}") from error
    try:
        table = ProfileTable.model_validate(raw)
    except Exception as error:
        raise ProfileError(f"the hardware profile table is malformed: {error}") from error
    return table.profiles


def measured_profiles_path(home: Path | None = None) -> Path:
    """Where a machine's own measurements live: in the archive, never in the repository.

    A figure measured on somebody's laptop is about their laptop. Writing it back into the
    committed table would publish a fact about their machine and would also mean the
    table's rows no longer all came from a citable page.
    """

    return ArchivLayout.resolve(home).config / "hardware-measured.json"


def load_measured_profiles(home: Path | None = None) -> list[HardwareProfile]:
    path = measured_profiles_path(home)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"cannot read this archive's measured profiles: {error}") from error
    payload = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    rows = payload.get("profiles")
    if not isinstance(rows, list):
        raise ProfileError("this archive's measured profiles file has no profiles list")
    return [HardwareProfile.model_validate(row) for row in cast("list[object]", rows)]


def save_measured_profile(profile: HardwareProfile, home: Path | None = None) -> Path:
    """Record a measurement for this machine, replacing any earlier one with the same id."""

    if profile.confidence != "measured":
        raise ProfileError("only a measured profile belongs in an archive's own record")
    existing = {row.id: row for row in load_measured_profiles(home)}
    existing[profile.id] = profile
    path = measured_profiles_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "note": (
            "Throughput measured on this machine by 'archiv model calibrate --local'. "
            "Not published figures, and not about anybody else's hardware."
        ),
        "profiles": [row.model_dump(mode="json") for row in existing.values()],
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def available_profiles(
    home: Path | None = None, published: Path | None = None
) -> list[HardwareProfile]:
    """This machine's own measurements first, then the published ones it has not replaced."""

    measured = load_measured_profiles(home)
    taken = {row.id for row in measured}
    return measured + [row for row in load_published_profiles(published) if row.id not in taken]


def find_profile(profile_id: str, profiles: list[HardwareProfile]) -> HardwareProfile:
    for row in profiles:
        if row.id == profile_id:
            return row
    known = ", ".join(sorted(row.id for row in profiles))
    raise ProfileError(
        f"no hardware profile called {profile_id!r}. Archiv will not guess a throughput "
        f"figure for hardware it has no measurement of. Known profiles: {known}"
    )


def bandwidth_anchor(profile: HardwareProfile) -> BandwidthAnchor | Unavailable:
    """Estimate generation speed from memory bandwidth, as a check on the published figure.

    Every generated token requires reading the whole model, so how fast the weights can be
    read off memory sets a ceiling on generation. This is a bound on the range rather than
    a point prediction, which is why it is reported beside the published figure instead of
    replacing it.
    """

    if profile.memory_bandwidth_bytes_per_second is None or profile.model_file_bytes is None:
        return Unavailable(
            status="not_derivable",
            reason=(
                "this profile carries no memory bandwidth figure or no model file size, so "
                "the second estimate cannot be worked out and the prediction rests on the "
                "published figure alone"
            ),
        )
    reads_per_second = profile.memory_bandwidth_bytes_per_second / profile.model_file_bytes
    low, high = CONSUMER_MEMORY_BANDWIDTH_UTILISATION_BAND
    return BandwidthAnchor(
        decode_tokens_per_second_low=round(reads_per_second * low, 4),
        decode_tokens_per_second_middle=round(
            reads_per_second * CONSUMER_MEMORY_BANDWIDTH_UTILISATION, 4
        ),
        decode_tokens_per_second_high=round(reads_per_second * high, 4),
        utilisation_assumed=CONSUMER_MEMORY_BANDWIDTH_UTILISATION,
        utilisation_band=CONSUMER_MEMORY_BANDWIDTH_UTILISATION_BAND,
        model_file_bytes=profile.model_file_bytes,
        memory_bandwidth_bytes_per_second=profile.memory_bandwidth_bytes_per_second,
    )


def predict_ask_latency(
    profile: HardwareProfile,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    retrieval_ms: float,
) -> PredictedLatency:
    """Predict one question's wall clock from a measured workload and a throughput profile.

    Reading the prompt and producing the reply are added separately and reported
    separately, because on the hardware somebody is most likely to buy first the first
    term dominates and the second is almost irrelevant.
    """

    if prompt_tokens < 0 or completion_tokens < 0:
        raise ProfileError("token counts cannot be negative")
    if retrieval_ms < 0:
        raise ProfileError("retrieval time cannot be negative")

    prefill_ms = prompt_tokens / profile.prefill_tokens_per_second.middle * 1000
    decode_ms = completion_tokens / profile.decode_tokens_per_second.middle * 1000

    anchor = bandwidth_anchor(profile)
    disagreement: float | None = None
    note = ""
    if isinstance(anchor, BandwidthAnchor):
        published = profile.decode_tokens_per_second.middle
        derived = anchor.decode_tokens_per_second_middle
        disagreement = round(abs(published - derived) / max(published, derived), 4)
        if disagreement > ANCHOR_DISAGREEMENT_THRESHOLD:
            note = (
                f"The published figure ({published:.2f} tokens a second) and the figure "
                f"derived from memory bandwidth ({derived:.2f}) differ by "
                f"{disagreement * 100:.0f}%. That disagreement is the honest uncertainty "
                "here and is reported rather than averaged away."
            )

    return PredictedLatency(
        label="measured" if profile.confidence == "measured" else "estimated",
        profile_id=profile.id,
        hardware=profile.hardware,
        model=profile.model,
        quantisation=profile.quantisation,
        source_url=profile.source_url,
        retrieved_on=profile.retrieved_on,
        measured_on_machine=profile.measured_on_machine,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        retrieval_ms=round(retrieval_ms, 3),
        time_to_first_token=_band(retrieval_ms + prefill_ms),
        generation=_band(decode_ms),
        total=_band(retrieval_ms + prefill_ms + decode_ms),
        second_anchor=anchor,
        anchors_disagree_by=disagreement,
        anchor_disagreement_note=note,
    )
