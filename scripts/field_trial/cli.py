"""Command-line entry point for the Archiv field trial."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from field_trial.common import DEFAULT_BENCHMARK, BenchmarkError
from field_trial.runner import run_private_trial, run_public_trial
from field_trial.source_location import apply_source_location_probe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--public", action="store_true")
    mode.add_argument("--corpus", type=Path)
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--archiv-command", default="archiv")
    parser.add_argument("--evidence-limit", type=int, default=8)
    parser.add_argument("--model-endpoint")
    parser.add_argument("--model-name")
    parser.add_argument("--render-report", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.public:
            args.output = args.output or Path("field-trial-artifacts")
            summary = run_public_trial(args)
            summary = apply_source_location_probe(
                summary, output=args.output, archiv_command=args.archiv_command
            )
            aggregate = cast(Mapping[str, object], summary["aggregate"])
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "questions": summary["question_count"],
                        "dominant_failure": aggregate["dominant_failure"],
                        "output": str(args.output),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(run_private_trial(args), sort_keys=True))
    except (BenchmarkError, OSError, RuntimeError, ValueError) as error:
        print(f"field trial failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
