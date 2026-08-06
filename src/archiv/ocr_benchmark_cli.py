"""CLI registration for the local OCR engine comparison."""

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
        engines: Annotated[
            str,
            typer.Option(
                "--engines",
                help="Comma-separated fixed engine set: tesseract, rapidocr, kraken.",
            ),
        ] = "tesseract",
        private_corpus: Annotated[
            Path | None,
            typer.Option(
                "--private-corpus",
                file_okay=False,
                dir_okay=True,
                resolve_path=True,
                help=(
                    "Local corpus directory containing manifest.json; content is never copied "
                    "into shareable output."
                ),
            ),
        ] = None,
    ) -> None:
        """Compare local OCR candidates on the same lawful corpus."""

        selected_candidates = [item.strip() for item in candidates.split(",") if item.strip()]
        selected_engines = [item.strip() for item in engines.split(",") if item.strip()]
        try:
            report = run_benchmark(
                output,
                selected_candidates or None,
                selected_engines,
                private_corpus,
            )
        except (OSError, OcrBenchmarkError, ValueError, json.JSONDecodeError) as error:
            typer.echo(f"OCR benchmark failed: {type(error).__name__}: {error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo(
            json.dumps(
                {
                    "status": "succeeded",
                    "recommended_candidate": report["recommended_candidate"],
                    "ranking": report["ranking"],
                    "report_path": report["report_path"],
                    "report_sha256": report["report_sha256"],
                    "summary_path": report["summary_path"],
                    "shareable_summary_path": report["shareable_summary_path"],
                    "shareable_summary_sha256": report["shareable_summary_sha256"],
                    "target_hardware_status": report["target_hardware_status"],
                },
                indent=2,
                sort_keys=True,
            )
        )

    app.command("benchmark-ocr")(benchmark_ocr_command)
