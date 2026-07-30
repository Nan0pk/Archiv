# Models propose; validators decide

## Decision

Archiv treats model output and agent-loop status as untrusted proposals. A run succeeds only when independent validators confirm the required artifacts, source integrity, calculations, citations, and policy conditions.

## Reason

Prior clean-room testing proved that a viable local model could perform an exact task while heavyweight harnesses timed out, failed before actuation, or reported completion without the required artifact.

## Consequences

- validators remain outside model prompts;
- required evidence is machine-readable;
- false completion is a regression failure;
- interfaces cannot override validator state;
- planning and retries are evaluated separately from capability correctness.
