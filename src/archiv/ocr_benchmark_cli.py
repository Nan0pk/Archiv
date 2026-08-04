"""CLI registration for the local OCR benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from archiv.ocr_benchmark import OcrBenchmarkError, run_benchmark


def register_ocr_benchmark_command(app: typer.Typer) -> None:
    """Register the operator-facing OCR benchmark command."""

    def benchmark_ocr_command(
        output: Annotated[
            Path,
            typer.Option(
                "--output",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help="Directory for generated fixtures and benchmark evidence.",
            ),
        ] = Path("ocr-benchmark"),
        candidates: Annotated[
            str,
            typer.Option(
                "--candidates",
                help="Comma-separated Tesseract language candidates; auto-detected when omitted.",
            ),
        ] = "",
    ) -> None:
        """Measure installed Tesseract language configurations on lawful fixtures."""

        selected = [item.strip() for item in candidates.split(",") if item.strip()]
        try:
            report = run_benchmark(output, selected or None)
        except (OSError, OcrBenchmarkError, ValueError) as error:
            typer.echo(f"OCR benchmark failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo(
            json.dumps(
                {
                    "status": "succeeded",
                    "engine": report["engine"],
                    "engine_version": report["engine_version"],
                    "recommended_candidate": report["recommended_candidate"],
                    "aggregates": report["aggregates"],
                    "report_path": report["report_path"],
                    "report_sha256": report["report_sha256"],
                },
                indent=2,
                sort_keys=True,
            )
        )

    app.command("benchmark-ocr")(benchmark_ocr_command)
