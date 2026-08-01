#!/usr/bin/env python3
"""Run Archiv's public benchmark or an explicitly opted-in private local trial."""

from field_trial.cli import main
from field_trial.common import BenchmarkError, load_benchmark, sha256_file
from field_trial.fixtures import FakeModelServer, generate_public_corpus
from field_trial.runner import (
    _copy_private_corpus,
    redact_private,
    run_public_trial,
    validate_private_request,
)
from field_trial.scoring import (
    _markdown,
    calculate_retrieval_metrics,
    scan_safe_artifacts,
    score_answer,
    validate_structural_citations,
)

__all__ = [
    "BenchmarkError",
    "FakeModelServer",
    "_copy_private_corpus",
    "_markdown",
    "calculate_retrieval_metrics",
    "generate_public_corpus",
    "load_benchmark",
    "redact_private",
    "run_public_trial",
    "scan_safe_artifacts",
    "score_answer",
    "sha256_file",
    "validate_private_request",
    "validate_structural_citations",
]

if __name__ == "__main__":
    raise SystemExit(main())
