"""Make schema enforcement explicit, and schema failure loud, counted and bounded.

Archiv's grounded answer is not prose. The model is asked for a strict JSON object and
anything that does not parse into `GroundedModelResponse` is rejected, so JSON adherence
is load-bearing: when it degrades, `ask` returns a broken-looking result.

The failure this module exists to prevent is a quiet one. Swapping the model behind
Archiv can silently stop schema enforcement from happening at all -- the request is
accepted, the reply comes back with a success code, and nothing anywhere says the
constraint was dropped. It then looks exactly like the new model being worse, and gets
misdiagnosed as a quality problem. So enforcement is a capability a backend has to
declare, and a run records whether it was actually in effect.

Two counters, deliberately kept apart:

- `schema_violation` -- the reply did not parse into the schema.
- `valid_but_unsupported` -- the reply parsed fine but cited evidence outside the set it
  was given.

Collapsing them into one "failure" number would hide the second, and the second is the
one that gets worse as models get smaller: constraining output buys validity, not
correctness, and a schema-valid answer citing the wrong thing looks like a success from
every angle except the one that matters.
"""

from __future__ import annotations

from typing import Literal, Protocol

from archiv.contracts import StrictModel
from archiv.grounding_contracts import GroundedModelResponse

DEFAULT_MAX_ATTEMPTS = 3
"""Small and fixed. A budget that grows with failure turns one bad reply into a bill."""

AttemptOutcome = Literal["accepted", "schema_violation", "valid_but_unsupported"]


class SchemaEnforcementUnavailableError(RuntimeError):
    """Raised when enforcement was required and the backend cannot provide it.

    This is the loud failure. The alternative -- carrying on unenforced -- is the exact
    silent degradation this module exists to prevent.
    """


class CompletionAdapter(Protocol):
    """The only thing this module needs a model backend to do."""

    def complete(self, prompt: str) -> str: ...


class SchemaCapableAdapter(Protocol):
    """A backend that has declared whether it can hold a reply to a schema."""

    def can_enforce_schema(self) -> bool: ...


def adapter_can_enforce_schema(adapter: object) -> bool:
    """Pessimistic by default: anything that has not said yes is treated as no.

    A backend added later, or a stand-in written for a test, inherits `False` rather than
    inheriting a promise nobody checked.
    """

    declare = getattr(adapter, "can_enforce_schema", None)
    if not callable(declare):
        return False
    return bool(declare())


class StructuredOutputAttempt(StrictModel):
    """One request to the model, and what came back."""

    attempt: int
    outcome: AttemptOutcome
    errors: list[str] = []


class StructuredOutputRecord(StrictModel):
    """Per-run evidence of how hard it was to get a usable answer out of the model."""

    schema_version: str = "1"
    enforcement_available: bool
    enforcement_required: bool
    attempts: int
    max_attempts: int
    schema_violation: int = 0
    valid_but_unsupported: int = 0
    unsupported_statements: int = 0
    """Paragraphs or claims in an accepted reply carrying no citation at all.

    Counted, not rejected. Rejecting it would change what `ask` accepts, which is not
    this module's job -- but leaving it uncounted would hide the failure mode that grows
    as models shrink.
    """
    attempt_log: list[StructuredOutputAttempt] = []


class StructuredOutputResult(StrictModel):
    """The usable response, if there is one, and the record of getting it."""

    response: GroundedModelResponse | None = None
    errors: list[str] = []
    record: StructuredOutputRecord


def count_unsupported_statements(response: GroundedModelResponse) -> int:
    """Paragraphs and claims that assert something while citing nothing."""

    return sum(1 for item in response.paragraphs if not item.citation_ids) + sum(
        1 for item in response.claims if not item.citation_ids
    )


def _retry_prompt(prompt: str, errors: list[str]) -> str:
    """Feed the specific validation error back, rather than just asking again.

    Repeating an identical prompt and hoping for a different reply wastes the budget.
    """

    complaint = "\n".join(f"- {error}" for error in errors)
    return (
        f"{prompt}\n\n"
        "YOUR PREVIOUS REPLY WAS REJECTED. Fix exactly these problems and reply again "
        "with the JSON object alone:\n"
        f"{complaint}\n"
    )


def request_grounded_response(
    adapter: CompletionAdapter,
    prompt: str,
    allowed_citations: set[str],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    require_enforcement: bool = False,
) -> StructuredOutputResult:
    """Ask the model for a grounded answer, retrying a bounded number of times.

    Shared by `ask` and `report`, which do not otherwise share their model call, so that
    a fix to one cannot silently miss the other.
    """

    # Imported here because grounding imports this module; the parser is the one piece of
    # knowledge that belongs there and not here.
    from archiv.grounding import classify_grounded_response

    available = adapter_can_enforce_schema(adapter)
    if require_enforcement and not available:
        raise SchemaEnforcementUnavailableError(
            "a schema-enforced reply was required, but this model backend has not "
            "declared that it can enforce one. Archiv will not send the request and "
            "quietly hope the reply happens to be valid, because that failure is "
            "indistinguishable from the model simply being worse."
        )

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    record = StructuredOutputRecord(
        enforcement_available=available,
        enforcement_required=require_enforcement,
        attempts=0,
        max_attempts=max_attempts,
        attempt_log=[],
    )
    errors: list[str] = []
    current_prompt = prompt

    for attempt in range(1, max_attempts + 1):
        raw = adapter.complete(current_prompt)
        record.attempts = attempt
        response, failure, errors = classify_grounded_response(raw, allowed_citations)

        if response is not None and failure is None:
            record.attempt_log.append(StructuredOutputAttempt(attempt=attempt, outcome="accepted"))
            record.unsupported_statements = count_unsupported_statements(response)
            return StructuredOutputResult(response=response, errors=[], record=record)

        outcome: AttemptOutcome = failure or "schema_violation"
        if outcome == "schema_violation":
            record.schema_violation += 1
        else:
            record.valid_but_unsupported += 1
        record.attempt_log.append(
            StructuredOutputAttempt(attempt=attempt, outcome=outcome, errors=list(errors))
        )
        current_prompt = _retry_prompt(prompt, errors)

    return StructuredOutputResult(response=None, errors=errors, record=record)
