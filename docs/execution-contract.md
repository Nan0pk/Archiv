# Execution contract

## Terminal states

Every run ends in exactly one state:

- `succeeded`;
- `failed`;
- `cancelled`;
- `waiting_for_approval`;
- `partially_produced_but_invalid`;
- `blocked_by_policy`.

A required artifact that does not exist can never produce `succeeded`.

## Capability declaration

Each capability must declare:

```yaml
name:
purpose:
input_schema:
output_schema:
reads:
writes:
forbidden_paths:
network_policy:
timeout_policy:
evidence_produced:
validator:
```

## Run evidence

A consequential run should be reproducible from an evidence directory containing, as applicable:

```text
request.json
resolved-policy.json
model-request.json
model-response.json
tool-calls.jsonl
file-changes.json
source-hashes.json
validation.json
result.json
logs/
```

## Completion rule

Success requires all mandatory validators to pass. The executor, agent, model, or interface may report progress, but none may override a failed validator.

## Retry rule

Retries must retain enough time for measured local-model latency. Deadlines must not shrink below the duration already demonstrated to be necessary. Model, transport, capability, validation, cancellation, and policy failures remain distinct.
