"""Keep the README's file-format table and command list true to the code.

Both blocks are written from what the program really contains: the format table from
``docs/format-compatibility.json`` and the command list from the ``archiv`` command tree.
A block sits between two marker comments in the README, and everything between the markers
is replaced. Everything outside the markers is ordinary hand-written text.

    python scripts/generate_readme_blocks.py            # rewrite the blocks
    python scripts/generate_readme_blocks.py --check    # exit 1 if any block is out of date

``tests/test_readme_matches_code.py`` runs the check, so a change that adds a format or a
command cannot land with a README that has not caught up.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any, cast

import typer

from archiv.cli import app

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MATRIX = ROOT / "docs" / "format-compatibility.json"

NOTICE = (
    "<sub>This block is written by `scripts/generate_readme_blocks.py` from {source}. "
    "Edit those, not the block.</sub>"
)

# How each family is named for a reader, and the one limit worth knowing before relying on it.
# Every limit below is the plain-words form of an entry in `known_limits` in the matrix.
# The test fails when a family is added without a line here, so a new format cannot be
# presented without someone deciding what to say about it.
FAMILIES: dict[str, tuple[str, str]] = {
    "plain-text": ("Plain text", "A citation points to a line"),
    "pdf": (
        "PDF",
        "Scanned pages need Tesseract and Poppler; Arabic-script text is a known problem (below)",
    ),
    "office-word": ("Word", "Text inside tracked insertions and content controls is not read"),
    "legacy-office-word": (
        "Word, old format",
        "No styles, comments, tracked changes or table structure",
    ),
    "legacy-rtf": ("Rich Text Format", "Headers, footers, footnotes and tables are dropped"),
    "office-spreadsheet": ("Excel", "Charts, images, comments and pivot state are not read"),
    "legacy-office-spreadsheet": (
        "Excel, old format",
        "Only the last calculated value of a formula; no charts",
    ),
    "office-presentation": (
        "PowerPoint",
        "Hidden slides are read and marked; alt text, comments and SmartArt are not read",
    ),
    "legacy-office-presentation": (
        "PowerPoint, old format",
        "No speaker notes or animation; slide numbers may differ after edits",
    ),
    "opendocument-text": ("OpenDocument text", "Layout, frames and styles are not rebuilt"),
    "opendocument-spreadsheet": ("OpenDocument spreadsheet", "Charts and pivot state are not read"),
    "opendocument-presentation": (
        "OpenDocument presentation",
        "Chart values are not read; hidden slides are read and marked",
    ),
    "opendocument-drawing": (
        "OpenDocument drawing",
        "Vector shapes are not rebuilt; the text in them is searchable",
    ),
    "opendocument-formula": (
        "OpenDocument formula",
        "The formula source is kept; no rendered equation",
    ),
    "opendocument-database": (
        "OpenDocument database",
        "Only the names of database objects are searchable",
    ),
    "inpage": ("InPage (Urdu)", "Text only; page layout is not rebuilt"),
    "image": (
        "Images",
        "Needs Tesseract and language data; without them the picture is kept but has no text",
    ),
    "svg": ("SVG drawings", "Text drawn as shapes is not read"),
    "audio": ("Audio", "Details only; the sound is not transcribed"),
    "archive": ("Archives", "Opened up to 8 levels and 128 MB; encrypted archives are not opened"),
}

HOW_READ = {
    "native": "Read directly",
    "visual_ocr_conditional": "Local OCR, when Tesseract is installed",
    "metadata_only": "Details only, no text",
}
SUPPORT = {"full": "✅ Full", "partial": "🟡 Partial"}

# Command list order. Every visible command must appear exactly once; the test and this
# script both fail on a command that is missing or unknown.
COMMAND_GROUPS: list[tuple[str, str, list[str]]] = [
    ("Everyday use", "", ["add", "find", "ask", "source", "report", "status", "ui"]),
    (
        "Files and formats",
        "",
        [
            "ingest",
            "formats",
            "benchmark-ocr",
            "rebuild-derived",
            "rebuild-search-index",
            "search",
            "process",
        ],
    ),
    (
        "Images, faces and the entity graph",
        "These read the same preserved originals and derived text as everything else. None of "
        "them can create, edit or replace an original.",
        [
            "images rebuild-index",
            "images find-similar",
            "images duplicates",
            "images status",
            "faces opt-in",
            "faces opt-out",
            "faces status",
            "faces scan",
            "faces list",
            "faces forget",
            "who",
            "graph rebuild",
            "graph stats",
            "graph query",
            "graph entity",
        ],
    ),
    (
        "Memory (separate, optional track)",
        "See [the design](docs/institutional-memory-design.md). The `--model-cmd` you give these "
        "commands decides where document text goes; see the privacy section of the README.",
        ["memory build", "memory ask", "memory probe"],
    ),
    (
        "A model on your own machine",
        "",
        [
            "model configure",
            "model configure-loopback",
            "model status",
            "model show",
            "model test",
            "model disable",
            "model profiles",
            "model calibrate",
        ],
    ),
    (
        "A model off this machine (evaluation archives only)",
        "Read [Evaluation archives](#evaluation-archives) before using any of these.",
        [
            "model evaluation status",
            "model evaluation enable",
            "model evaluation disable",
            "model configure-remote-evaluation",
            "model spend set",
            "model spend status",
            "model spend reset",
        ],
    ),
    ("Keeping and moving your data", "", ["backup", "export", "restore", "sample-vault"]),
    (
        "Tasks, checks and reports",
        "",
        [
            "doctor",
            "version",
            "run",
            "verify",
            "generate-report",
            "verify-report",
            "source-marker",
            "diagnostics-export",
        ],
    ),
]


# Typer builds its own command classes, so groups and arguments are told apart by what they
# have, not by their class.
Command = Any


def _command_tree() -> Command:
    return typer.main.get_command(app)


def visible_commands() -> dict[str, Command]:
    """Every visible command, keyed by its full words (``model spend set``)."""
    found: dict[str, Command] = {}

    def walk(group: Command, prefix: str) -> None:
        for name, command in sorted(group.commands.items()):
            if command.hidden:
                continue
            path = f"{prefix} {name}".strip()
            if hasattr(command, "commands"):
                walk(command, path)
            else:
                found[path] = command

    walk(_command_tree(), "")
    return found


# Where the command's own help text says more than has been verified, the README says less.
# (The help text itself is source wording and is fixed separately.) Every key must be a real
# command; render_commands stops if one is not.
SUMMARY_OVERRIDES: dict[str, str] = {}


def _usage(path: str, command: Command) -> str:
    words = [path]
    for param in command.params:
        if getattr(param, "param_type_name", "") == "argument" and param.name:
            name = str(param.name).upper()
            words.append(name if param.required else f"[{name}]")
    return "archiv " + " ".join(words)


def _summary(command: Command) -> str:
    text = inspect.cleandoc(command.help or "").split("\n\n")[0]
    return " ".join(text.split()).replace("|", "\\|")


def render_commands() -> str:
    commands = visible_commands()
    listed = [path for _t, _i, paths in COMMAND_GROUPS for path in paths]
    missing = sorted(set(commands) - set(listed))
    unknown = sorted(set(listed) - set(commands))
    duplicated = sorted({p for p in listed if listed.count(p) > 1})
    if missing or unknown or duplicated:
        raise SystemExit(
            "COMMAND_GROUPS in scripts/generate_readme_blocks.py is out of step with the "
            f"command tree. Missing: {missing}. Unknown: {unknown}. Listed twice: {duplicated}."
        )
    stray = sorted(set(SUMMARY_OVERRIDES) - set(commands))
    if stray:
        raise SystemExit(f"SUMMARY_OVERRIDES names commands that do not exist: {stray}.")
    out = [
        NOTICE.format(
            source="the `archiv` command tree and the wording in `SUMMARY_OVERRIDES` in that script"
        ),
        "",
    ]
    for title, intro, paths in COMMAND_GROUPS:
        out.append(f"#### {title}")
        out.append("")
        if intro:
            out += [intro, ""]
        out += ["| Command | What it does |", "| --- | --- |"]
        for path in paths:
            command = commands[path]
            summary = SUMMARY_OVERRIDES.get(path) or _summary(command)
            out.append(f"| `{_usage(path, command)}` | {summary} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_formats() -> str:
    matrix = cast(dict[str, Any], json.loads(MATRIX.read_text(encoding="utf-8")))
    families = cast(list[dict[str, Any]], matrix["families"])
    ids = [str(f["family"]) for f in families]
    absent = sorted(set(ids) - set(FAMILIES))
    stale = sorted(set(FAMILIES) - set(ids))
    if absent or stale:
        raise SystemExit(
            "FAMILIES in scripts/generate_readme_blocks.py is out of step with "
            f"docs/format-compatibility.json. No wording for: {absent}. Not in the matrix: {stale}."
        )
    suffixes = sorted({s for f in families for s in cast(list[str], f["suffixes"])})
    rejected = cast(list[dict[str, str]], matrix["rejected_examples"])
    out = [
        NOTICE.format(
            source="`docs/format-compatibility.json` and the wording in `FAMILIES` in that script"
        ),
        "",
        f"**{len(suffixes)} file types** in {len(families)} families are accepted. "
        "The support level of each family is as recorded in the matrix: ✅ **Full** or "
        "🟡 **Partial**. The last column names the main limit, and `archiv formats TYPE` "
        "lists them all.",
        "",
        "| Kind | Extensions | Support | How the text is read | Worth knowing |",
        "| --- | --- | --- | --- | --- |",
    ]
    for family in families:
        name, limit = FAMILIES[str(family["family"])]
        extensions = " ".join(f"`{s}`" for s in cast(list[str], family["suffixes"]))
        support = SUPPORT[str(family["support_level"])]
        how = HOW_READ[str(family["text_extraction"])]
        out.append(f"| {name} | {extensions} | {support} | {how} | {limit} |")
    out.append("")
    refused = ", ".join(f"`{r['suffix']}` ({r['example']})" for r in rejected)
    out.append(
        f"Turned away before any parsing: {refused}. Anything else is refused as unsupported."
    )
    return "\n".join(out) + "\n"


def _fill(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    try:
        head, rest = text.split(start, 1)
        _old, tail = rest.split(end, 1)
    except ValueError:
        raise SystemExit(f"Missing the {start} / {end} markers for the {name} block.") from None
    return f"{head}{start}\n{body}{end}{tail}"


def build() -> dict[Path, str]:
    """The text each generated file should have."""
    text = README.read_text(encoding="utf-8")
    text = _fill(text, "formats", render_formats())
    text = _fill(text, "commands", render_commands())
    return {README: text}


def out_of_date() -> list[Path]:
    return [path for path, text in build().items() if path.read_text(encoding="utf-8") != text]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="exit 1 if a block is out of date")
    args = parser.parse_args(argv)
    if args.check:
        stale = out_of_date()
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)}", file=sys.stderr)
        if stale:
            print("run: python scripts/generate_readme_blocks.py", file=sys.stderr)
        return 1 if stale else 0
    for path, text in build().items():
        if path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print(f"updated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
