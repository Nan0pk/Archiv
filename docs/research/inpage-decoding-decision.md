# Native InPage decoding decision

## Decision

Continue with bounded research-only direct extraction. Do not register `.inp`, connect
the decoder to ingestion, or close issue #38.

## Why

Public candidates and PR #49 now provide strong evidence that:

- `InPage300` can contain substantial aligned Unicode/Perso-Arabic text;
- `InPage100` contains repeated escape and record-like structures.

They do not establish creator version/build, lawful redistribution, exact text,
reading order, layout structure, protection markers or support boundaries.

## Next gate

1. Reproduce all PR #49 measurements on pinned bytes.
2. Measure the InPage100 two-byte versus four-byte record-header discrepancy.
3. Compare independent mapping sources and publish conflicts.
4. Decode at least two InPage300 and three InPage100 candidates privately and
   deterministically.
5. Validate InPage300 against a pinned lawful Quran corpus under separately reported
   normalizations.
6. Obtain lawful redistributable known-version fixtures with human-reviewed text and
   layout truth before production implementation.

## Stop conditions

- Recommend a narrow InPage300 parser only after independent text validation passes.
- Recommend a narrow InPage100 parser only after framing and mapping validation pass.
- Otherwise document the exact remaining blocker and retain explicit rejection.
