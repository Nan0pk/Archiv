"""Command line for the institutional memory: `archiv memory …`."""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer

from archiv.memory.ask import ask
from archiv.memory.build import build_memory, passage_vectors_base
from archiv.memory.model import Complete, command_completer
from archiv.memory.probe import draw_probes
from archiv.memory.store import open_memory
from archiv.memory.vectors import Embedder, EmbedderUnavailable, FastEmbedder
from archiv.search.service import search_documents
from archiv.storage.layout import ArchivLayout

memory_app = typer.Typer(
    no_args_is_help=True,
    help="Build a web of things and connections from the archive, and ask it questions.",
)

HomeOption = Annotated[Path | None, typer.Option("--home", file_okay=False)]
ModelCmdOption = Annotated[
    str | None,
    typer.Option(
        "--model-cmd",
        help="Command that reads a prompt on standard input and prints the answer. "
        "Defaults to $ARCHIV_MODEL_CMD.",
    ),
]
ModelLabelOption = Annotated[
    str,
    typer.Option(
        "--model-label", help="Name for the model behind --model-cmd; part of the cache key."
    ),
]


def _completer(model_cmd: str | None) -> Complete:
    command = model_cmd or os.environ.get("ARCHIV_MODEL_CMD")
    if not command:
        typer.echo(
            "No model command given. Pass --model-cmd or set ARCHIV_MODEL_CMD to a command "
            "that reads a prompt on standard input and prints the answer.",
            err=True,
        )
        raise typer.Exit(2)
    return command_completer(shlex.split(command))


def _embedder() -> Embedder | None:
    try:
        return FastEmbedder()
    except EmbedderUnavailable as error:
        typer.echo(f"Meaning vectors skipped. {error}", err=True)
        return None


@memory_app.command("build")
def build_command(
    model_label: ModelLabelOption,
    home: HomeOption = None,
    model_cmd: ModelCmdOption = None,
    max_chars: Annotated[int, typer.Option("--max-chars", min=200)] = 6000,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Read every document through the model and store what it mentions and how it connects."""
    summary = build_memory(
        home=home,
        complete=_completer(model_cmd),
        model_label=model_label,
        max_chars=max_chars,
        embedder=_embedder(),
    )
    if json_output:
        typer.echo(json.dumps(asdict(summary), indent=2))
        return
    typer.echo(
        f"Read {summary.documents} documents: {summary.model_calls} model calls, "
        f"{summary.cache_hits} answers reused from earlier runs, "
        f"{summary.failed_batches} batches the model did not answer usably.\n"
        f"The memory now holds {summary.things} things and {summary.relations} relations; "
        f"{summary.merges_confirmed} written forms were confirmed to be the same thing."
    )


@memory_app.command("ask")
def ask_command(
    question: Annotated[str, typer.Argument(help="Your question, in plain words.")],
    model_label: ModelLabelOption,
    home: HomeOption = None,
    model_cmd: ModelCmdOption = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Answer a question from what the memory holds, with a cited source for every claim."""
    layout = ArchivLayout.resolve(home)
    has_vectors = Path(f"{passage_vectors_base(layout)}.ids.json").is_file()
    report = ask(
        question,
        home=home,
        complete=_completer(model_cmd),
        model_label=model_label,
        embedder=_embedder() if has_vectors else None,
    )
    sources = [
        {
            "label": f"P{n}",
            "source": p.source_name,
            "locator": p.locator,
            "text": p.text[:160],
        }
        for n, p in zip(report.labels, report.cited, strict=True)
    ]
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "report": report.text,
                    "sources": sources,
                    "unknown_labels": report.unknown_labels,
                    "uncited_sentences": report.uncited_sentences,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    typer.echo(report.text)
    if sources:
        typer.echo("\nSources:")
        for item in sources:
            typer.echo(f"  [{item['label']}] {item['source']} {item['locator']}: {item['text']}")
    if report.unknown_labels:
        typer.echo(
            "\nWarning: the report cites passages that were never given to the model: "
            + ", ".join(report.unknown_labels)
            + ". Do not trust those statements."
        )
    if report.uncited_sentences:
        typer.echo(
            f"\nWarning: {report.uncited_sentences} sentence(s) in the report have no source."
        )


@memory_app.command("probe")
def probe_command(
    model_label: ModelLabelOption,
    n: Annotated[int, typer.Option("--n", min=1, help="How many random questions.")] = 10,
    seed: Annotated[int, typer.Option("--seed", help="Same seed, same questions.")] = 1,
    out: Annotated[Path, typer.Option("--out", file_okay=False)] = Path("memory-probes"),
    home: HomeOption = None,
    model_cmd: ModelCmdOption = None,
) -> None:
    """Ask random questions drawn from the data and set each answer beside plain search."""
    layout = ArchivLayout.resolve(home)
    complete = _completer(model_cmd)
    has_vectors = Path(f"{passage_vectors_base(layout)}.ids.json").is_file()
    embedder = _embedder() if has_vectors else None
    with open_memory(layout) as conn:
        probes = draw_probes(conn, n=n, seed=seed)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for number, probe in enumerate(probes, start=1):
        name = f"probe-{number:02d}"
        report = ask(
            probe.question,
            home=home,
            complete=complete,
            model_label=model_label,
            embedder=embedder,
        )
        lines = [f"# {name}: {probe.question}", "", "## What the memory says", "", report.text, ""]
        for label, passage in zip(report.labels, report.cited, strict=True):
            lines.append(
                f"- [P{label}] {passage.source_name} {passage.locator}: {passage.text[:160]}"
            )
        if report.unknown_labels:
            lines.append(
                f"\nWarning: cites passages never given to the model: {report.unknown_labels}"
            )
        if report.uncited_sentences:
            lines.append(f"\nWarning: {report.uncited_sentences} sentence(s) have no source.")
        lines += ["", "## What plain search finds", ""]
        for result in search_documents(probe.form, home=home, limit=20):
            c = result.citation
            lines.append(f"- {c.source_name} {c.locator}: {result.text[:160]}")
        (out / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows.append(f"| {name} | {probe.form} |  |  |  |")
    judge = [
        "# Judge the answers",
        "",
        "For each probe, open its file and fill in the three blank columns.",
        "",
        "| probe | about | useful? | beyond search? | any claim wrong? |",
        "|---|---|---|---|---|",
        *rows,
    ]
    (out / "judge.md").write_text("\n".join(judge) + "\n", encoding="utf-8")
    typer.echo(f"Wrote {len(probes)} probes and judge.md to {out}")
