"""Make a remote model's cost visible before it is charged, and cap it structurally.

The remote evaluation path is the only one that costs money, and the retry loop above it
can send the same question up to three times. Nothing capped that. This adds a ceiling
that is checked before any network activity, and a record of what has actually been spent
so the ceiling means something across runs rather than per call.

Three things are refused rather than guessed at:

- **No prices, no remote call.** Published prices change, so a price a person did not
  enter is a number nobody measured. There is no default price table.
- **No ceiling, no remote call.** The ceiling is the point.
- **No token count from character length, ever.** A counted prompt or none at all.

Counting needs the provider's own tokenizer, and the obvious library fetches its encoding
over the network the first time it is asked for one. That is forbidden here -- no
processor may download anything at runtime -- so the encoding must be a pinned file whose
hash is recorded, exactly as installed OCR languages are. When it is absent the count is
skipped and says so, and the ceiling still holds on recorded spend and a hard cap on
output length. What is lost without it is the ability to refuse a single call for being
too large before making it; what is kept is that spending cannot run away.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import Field, model_validator

from archiv.contracts import StrictModel
from archiv.storage.layout import ArchivLayout

TokenizerStatus = Literal["pinned", "not-configured", "unavailable"]

SpendRefusalReason = Literal[
    "no-spend-policy",
    "spend-record-unreadable",
    "ceiling-already-reached",
    "would-exceed-ceiling",
]


class Tokenizer(Protocol):
    """The only thing this module needs from an encoding."""

    def encode(self, text: str) -> list[int]: ...


class SpendRefusedError(RuntimeError):
    """Base for every refusal to spend, so a caller can tell one from a real failure.

    A run stopped by the ceiling is a boundary doing its job. Without a common base, the
    only way to catch that was to name all three subclasses, and the retry path caught
    none of them -- so a refusal on a retry arrived as a generic model-request failure
    and the run was recorded as broken rather than as refused.
    """


class SpendPolicyMissingError(SpendRefusedError):
    """Raised when a remote call is attempted with no spend policy recorded."""


class SpendCeilingReachedError(SpendRefusedError):
    """Raised before any network activity when a call would exceed the ceiling."""


class SpendLedgerUnreadableError(SpendRefusedError):
    """Raised when what has been spent cannot be read, so the ceiling cannot be applied.

    Absent is not the same as unreadable. An absent ledger means nothing has been spent.
    An unreadable one means the total is unknown, and treating unknown as zero re-arms a
    ceiling that may already have been reached. It is also a reachable state rather than
    a theoretical one: `config/` travels in backups, and a ledger written by a later
    Archiv carrying a field this one does not know is unreadable here.
    """


class TokenPrices(StrictModel):
    """What the provider charges, as the person configuring it read them."""

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)
    recorded_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    """The date these were read off the provider's page. Prices change; a rate with no
    date is a number of unknown age being presented as current."""
    source: str = Field(min_length=1)


class PinnedTokenizer(StrictModel):
    """An encoding file already on disk, with its hash recorded.

    A path and a hash rather than a name to fetch. The library that would fetch it by
    name downloads at runtime, which this project forbids outright.
    """

    encoding_name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split_pattern: str = Field(min_length=1)
    """The encoding's own split rule, pinned alongside it.

    Not a constant in this module. An earlier version hardcoded a pattern and described
    it as the rule the supported encodings were built with; it was neither of theirs.
    A count produced by the wrong split rule is not the provider's count, and it would
    have been shown as a projected cost and used to refuse calls. Whoever pins the
    encoding pins its definition, and this module asserts nothing about encodings it has
    never loaded.
    """


class SpendPolicy(StrictModel):
    """What this archive is allowed to spend, and what it costs to spend it."""

    schema_version: str = "1"
    ceiling_usd: float = Field(gt=0)
    max_output_tokens: int = Field(gt=0, le=32_000)
    """A hard cap sent with every request, so the reply's cost is bounded even when the
    prompt cannot be counted."""
    prices: TokenPrices
    tokenizer: PinnedTokenizer | None = None


class SpendLedger(StrictModel):
    """What this archive has actually spent, accumulated across runs."""

    schema_version: str = "1"
    spent_usd: float = Field(default=0.0, ge=0)
    recorded_calls: int = Field(default=0, ge=0)
    unmeasured_calls: int = Field(default=0, ge=0)
    """Calls the provider did not report usage for. Counted separately rather than
    estimated, so the spend figure stays something that was measured."""


class CostPreflight(StrictModel):
    """What was known about a call's cost before it was made."""

    schema_version: str = "1"
    tokenizer_status: TokenizerStatus
    tokenizer_detail: str = ""
    prompt_tokens: int | None = None
    projected_input_usd: float | None = None
    projected_worst_case_usd: float | None = None
    """Input cost plus the maximum the reply can cost at the configured output cap."""
    spent_before_usd: float
    ceiling_usd: float
    max_output_tokens: int


class SpendDecision(StrictModel):
    """Whether a call may be made, and what that was decided from.

    Written into the run's evidence whether or not the call was allowed. An earlier
    version wrote the cost record as the argument to the check, so a refused run left no
    record at all -- which is exactly backwards. A refusal is the run somebody later
    asks about: what the ceiling was, what had already been spent, and whether there was
    a projection to refuse it on.
    """

    schema_version: str = "2"
    """Version two of this file. Version one was the pre-flight numbers alone, at the top
    level; they are now nested under `preflight` beside the decision that used them. A
    reader who cannot tell the two shapes apart by version has to guess, so the version
    moved."""
    decision: Literal["allowed", "refused"]
    refused_because: SpendRefusalReason | None = None
    explanation: str = ""
    """What the person is told, kept on disk as well as raised, so the numbers that
    refused the call outlive the error message on their terminal."""
    preflight: CostPreflight | None = None
    """The numbers the decision was made from. Absent only when there were none: no
    policy at all, or a spend record that could not be read."""

    @model_validator(mode="after")
    def _decision_matches_its_evidence(self) -> SpendDecision:
        if self.decision == "allowed":
            if self.refused_because is not None:
                raise ValueError("an allowed call cannot carry a reason for being refused")
            if self.preflight is None:
                raise ValueError("an allowed call must record the numbers it was allowed on")
        else:
            if self.refused_because is None:
                raise ValueError("a refused call must record what refused it")
            if not self.explanation.strip():
                raise ValueError("a refused call must record what the person was told")
        return self


class SpendRecord(StrictModel):
    """Every cost decision one run made, and what happened taken together.

    A run makes more than one when it retries: the layer above the model call can send
    the same question up to three times, and each attempt passes the gate again. An
    earlier version of this file held only the decision taken before the first attempt,
    so a run whose first attempt was allowed and whose retry was refused left a record
    saying the spending had been allowed -- next to a result saying the run failed, with
    the real reason nowhere but inside an error string.
    """

    schema_version: str = "3"
    outcome: Literal[
        "all-attempts-allowed",
        "refused-before-any-request",
        "refused-after-spending",
    ]
    """What happened, in terms that cannot be read the wrong way.

    Deliberately not "allowed" or "refused". A run whose first attempt was sent and
    billed, and whose second was refused, was neither: it spent money and was then
    stopped. Calling that "refused" hides the spending and calling it "allowed" hides the
    stop, so it has its own name.
    """
    refused_because: SpendRefusalReason | None = None
    explanation: str = ""
    attempts: list[SpendDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def _outcome_matches_its_attempts(self) -> SpendRecord:
        refused = [index for index, item in enumerate(self.attempts) if item.refused_because]
        if not refused:
            if self.outcome != "all-attempts-allowed":
                raise ValueError("no attempt was refused, so the run cannot record a refusal")
            if self.refused_because is not None:
                raise ValueError("no attempt was refused, so there is no reason to record")
            return self
        expected = "refused-before-any-request" if refused[0] == 0 else "refused-after-spending"
        if self.outcome != expected:
            raise ValueError(f"attempt {refused[0] + 1} was refused, so the outcome is {expected}")
        if self.refused_because is None or not self.explanation.strip():
            raise ValueError("a refused run must record what refused it, and what was said")
        return self


def summarise_spend(decisions: Sequence[SpendDecision]) -> SpendRecord:
    """Turn one run's decisions into the record written to its evidence."""

    if not decisions:
        raise ValueError("a spend record needs at least one decision")
    first_refusal = next((item for item in decisions if item.refused_because), None)
    if first_refusal is None:
        return SpendRecord(outcome="all-attempts-allowed", attempts=list(decisions))
    refused_at = decisions.index(first_refusal)
    return SpendRecord(
        outcome="refused-before-any-request" if refused_at == 0 else "refused-after-spending",
        refused_because=first_refusal.refused_because,
        explanation=first_refusal.explanation,
        attempts=list(decisions),
    )


def spend_policy_path(home: Path | None = None) -> Path:
    return ArchivLayout.resolve(home).config / "spend.json"


def spend_ledger_path(home: Path | None = None) -> Path:
    return ArchivLayout.resolve(home).config / "spend-ledger.json"


def _write_atomic(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_spend_policy(home: Path | None = None) -> SpendPolicy | None:
    """Read the policy. Absent or unreadable means no policy, which means no remote call."""

    path = spend_policy_path(home)
    if not path.is_file():
        return None
    try:
        return SpendPolicy.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_spend_policy(policy: SpendPolicy, home: Path | None = None) -> Path:
    return _write_atomic(spend_policy_path(home), policy.model_dump(mode="json"))


def load_ledger(home: Path | None = None) -> SpendLedger:
    """Read what has been spent. Absent means nothing; unreadable raises.

    Reading an unreadable ledger as zero would fail open on the one number the ceiling
    depends on, and would then let the next call overwrite the real total with that zero.
    """

    path = spend_ledger_path(home)
    if not path.is_file():
        return SpendLedger()
    try:
        return SpendLedger.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise SpendLedgerUnreadableError(
            f"the record of what this archive has spent cannot be read ({path}): "
            f"{type(error).__name__}: {error}\n"
            "Archiv will not make a paid request while the total is unknown, and will "
            "not overwrite the file. Repair it, or clear it deliberately with "
            "'archiv model spend reset --acknowledge-this-forgets-what-was-spent'."
        ) from error


def load_pinned_tokenizer(policy: SpendPolicy) -> tuple[Tokenizer | None, TokenizerStatus, str]:
    """Load the pinned encoding, or say why there is none. Never fetches anything."""

    if policy.tokenizer is None:
        return None, "not-configured", "no tokenizer recorded in the spend policy"

    pinned = policy.tokenizer
    path = Path(pinned.path).expanduser()
    if not path.is_file():
        return None, "unavailable", f"pinned encoding file is missing: {path}"

    from hashlib import sha256

    digest = sha256(path.read_bytes()).hexdigest()
    if digest != pinned.sha256:
        return (
            None,
            "unavailable",
            (f"pinned encoding file does not match its recorded hash: {path}"),
        )

    try:
        # Imported here, not at module scope: this is an optional extra, and installing
        # Archiv without it must keep working.
        import tiktoken
        import tiktoken.load
    except ImportError:
        return (
            None,
            "unavailable",
            (
                "tiktoken is not installed; install the remote-evaluation extra to count "
                "tokens from the pinned encoding"
            ),
        )

    try:
        ranks = tiktoken.load.load_tiktoken_bpe(str(path), expected_hash=pinned.sha256)
        encoding = cast(
            Tokenizer,
            tiktoken.Encoding(
                name=pinned.encoding_name,
                pat_str=pinned.split_pattern,
                mergeable_ranks=ranks,
                special_tokens={},
            ),
        )
    except Exception as error:  # noqa: BLE001 - any failure means no counting, never a fetch
        return None, "unavailable", f"pinned encoding could not be loaded: {error}"

    return encoding, "pinned", str(path)


def count_prompt_tokens(
    policy: SpendPolicy, prompt: str
) -> tuple[int | None, TokenizerStatus, str]:
    """Count the prompt, or return no count. Never estimates from length."""

    encoding, status, detail = load_pinned_tokenizer(policy)
    if encoding is None:
        return None, status, detail
    try:
        tokens = len(encoding.encode(prompt))
    except Exception as error:  # noqa: BLE001
        return None, "unavailable", f"pinned encoding failed to encode the prompt: {error}"
    return tokens, status, detail


def usd_for_tokens(tokens: int, per_million_usd: float) -> float:
    return round(tokens * per_million_usd / 1_000_000, 6)


_NO_POLICY_MESSAGE = (
    "This archive has no spend policy, so Archiv will not make a paid request.\n"
    "\n"
    "A remote model charges per call, and a question can be retried up to three times, "
    "so a run with no ceiling has no upper bound on what it costs.\n"
    "\n"
    "Set one with:\n"
    "    archiv model spend set --ceiling-usd <amount> "
    "--input-per-million <usd> --output-per-million <usd> "
    "--prices-recorded-on <YYYY-MM-DD> --prices-source <where you read them>\n"
    "There is no default price table: a rate nobody entered is a number nobody checked."
)


def decide_spend(prompt: str, home: Path | None = None) -> SpendDecision:
    """Decide whether this call may be made, and hand the decision back rather than raise.

    Every ending returns a record, so a caller can write what it found into the run's
    evidence and then act on it -- in that order. `check_spend_allowance` is this
    function plus the raise, for callers that only need the gate.
    """

    policy = load_spend_policy(home)
    if policy is None:
        return SpendDecision(
            decision="refused",
            refused_because="no-spend-policy",
            explanation=_NO_POLICY_MESSAGE,
        )

    try:
        ledger = load_ledger(home)
    except SpendLedgerUnreadableError as error:
        # Refused with no numbers, because there are none: the total this would be
        # measured against is the thing that could not be read.
        return SpendDecision(
            decision="refused",
            refused_because="spend-record-unreadable",
            explanation=str(error),
        )

    prompt_tokens, status, detail = count_prompt_tokens(policy, prompt)

    projected_input: float | None = None
    projected_worst: float | None = None
    if prompt_tokens is not None:
        projected_input = usd_for_tokens(prompt_tokens, policy.prices.input_per_million_usd)
        projected_worst = round(
            projected_input
            + usd_for_tokens(policy.max_output_tokens, policy.prices.output_per_million_usd),
            6,
        )

    preflight = CostPreflight(
        tokenizer_status=status,
        tokenizer_detail=detail,
        prompt_tokens=prompt_tokens,
        projected_input_usd=projected_input,
        projected_worst_case_usd=projected_worst,
        spent_before_usd=ledger.spent_usd,
        ceiling_usd=policy.ceiling_usd,
        max_output_tokens=policy.max_output_tokens,
    )

    if ledger.spent_usd >= policy.ceiling_usd:
        return SpendDecision(
            decision="refused",
            refused_because="ceiling-already-reached",
            explanation=(
                f"This archive has spent ${ledger.spent_usd:.4f} of its "
                f"${policy.ceiling_usd:.2f} ceiling, so Archiv will not make another paid "
                "request.\n"
                "Raise the ceiling with 'archiv model spend set --ceiling-usd <amount>', or "
                "reset what has been spent with 'archiv model spend reset' if the recorded "
                "total no longer reflects what you are being billed."
            ),
            preflight=preflight,
        )

    if projected_worst is not None and ledger.spent_usd + projected_worst > policy.ceiling_usd:
        return SpendDecision(
            decision="refused",
            refused_because="would-exceed-ceiling",
            explanation=(
                f"This request would cost up to ${projected_worst:.4f} "
                f"({prompt_tokens} prompt tokens plus at most {policy.max_output_tokens} in "
                f"reply), and ${ledger.spent_usd:.4f} of the ${policy.ceiling_usd:.2f} "
                "ceiling is already spent. Nothing has been sent.\n"
                "Raise the ceiling, or ask with fewer sources using --max-sources."
            ),
            preflight=preflight,
        )

    return SpendDecision(decision="allowed", preflight=preflight)


_REFUSAL_ERRORS: dict[SpendRefusalReason, type[RuntimeError]] = {
    "no-spend-policy": SpendPolicyMissingError,
    "spend-record-unreadable": SpendLedgerUnreadableError,
    "ceiling-already-reached": SpendCeilingReachedError,
    "would-exceed-ceiling": SpendCeilingReachedError,
}


def check_spend_allowance(
    prompt: str,
    home: Path | None = None,
    *,
    record_into: list[SpendDecision] | None = None,
) -> CostPreflight:
    """Decide whether this call may be made, before anything is sent, and raise if not.

    Sits beside the evaluation-marker check and is called from the same place, so the
    two read as one gate rather than two policies scattered across the path.

    `record_into` collects the decision whether it allowed the call or refused it, so the
    caller can write down every attempt rather than only the first. Deciding and raising
    stays in one place; only the keeping of the answer is the caller's.
    """

    decision = decide_spend(prompt, home)
    if record_into is not None:
        record_into.append(decision)
    if decision.refused_because is not None:
        raise _REFUSAL_ERRORS[decision.refused_because](decision.explanation)
    if decision.preflight is None:  # pragma: no cover - the model rejects this combination
        raise SpendPolicyMissingError("an allowed call arrived with no numbers behind it")
    return decision.preflight


def record_spend(
    input_tokens: int | None,
    output_tokens: int | None,
    home: Path | None = None,
) -> SpendLedger:
    """Add a completed call to the ledger, from the provider's reported usage.

    Usage the provider did not report is counted as an unmeasured call rather than
    estimated into the total, so the spend figure stays a measurement.
    """

    policy = load_spend_policy(home)
    if policy is None:
        raise SpendPolicyMissingError("cannot record spend without a spend policy")

    # Raises if the ledger is unreadable, which is what stops a corrupt file being
    # replaced by a fresh one that has forgotten the total.
    ledger = load_ledger(home)
    if input_tokens is None or output_tokens is None:
        updated = ledger.model_copy(
            update={
                "recorded_calls": ledger.recorded_calls + 1,
                "unmeasured_calls": ledger.unmeasured_calls + 1,
            }
        )
    else:
        cost = usd_for_tokens(input_tokens, policy.prices.input_per_million_usd) + usd_for_tokens(
            output_tokens, policy.prices.output_per_million_usd
        )
        updated = ledger.model_copy(
            update={
                "spent_usd": round(ledger.spent_usd + cost, 6),
                "recorded_calls": ledger.recorded_calls + 1,
            }
        )
    _write_atomic(spend_ledger_path(home), updated.model_dump(mode="json"))
    return updated


def reset_ledger(home: Path | None = None) -> SpendLedger:
    """Clear the recorded spend, keeping the policy."""

    fresh = SpendLedger()
    _write_atomic(spend_ledger_path(home), fresh.model_dump(mode="json"))
    return fresh
