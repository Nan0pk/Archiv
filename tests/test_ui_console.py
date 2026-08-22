"""Acceptance tests for the minimal local test console (issue #63).

The console must derive its schema from the real Typer tree, build injection-
proof argument vectors, run one command at a time with shell=False, retain
complete output, open only verified artifacts, and resolve citations through
Archiv's bounded source-location validator.  CLI contracts stay unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Literal

import pytest
import typer
from typer.testing import CliRunner

from archiv.cli import app as cli_app
from archiv.hashing import sha256_file
from archiv.ui.argv import build_argv, quoted_invocation
from archiv.ui.console_schema import (
    ParameterKind,
    collect_console_schema,
)
from archiv.ui.desktop import open_with_default_handler
from archiv.ui.errors import UiError
from archiv.ui.form_plan import BrowseMode, control_plan
from archiv.ui.outputs import (
    collect_user_paths,
    inspect_run_output,
    parse_run_json,
    resolve_citation_path,
    verify_artifact,
)
from archiv.ui.runner import ConsoleRunner, console_executable_argv

runner = CliRunner()


def _fixture_app() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)

    @app.command("plain")
    def plain_command(
        source: Annotated[
            Path,
            typer.Argument(exists=True, file_okay=True, dir_okay=True),
        ],
        note: Annotated[str, typer.Option("--note")] = "default note",
    ) -> None:
        """Run with one path argument and one text option."""

    @app.command("typed")
    def typed_command(
        count: Annotated[int, typer.Option("--count", min=1, max=9)] = 3,
        ratio: Annotated[float, typer.Option("--ratio")] = 0.5,
        level: Annotated[str, typer.Option("--level")] = "low",
        force: Annotated[bool, typer.Option("--force")] = False,
        dual: Annotated[bool, typer.Option("--dual/--no-dual")] = True,
        out_file: Annotated[
            Path, typer.Option("--out-file", file_okay=True, dir_okay=False)
        ] = Path("out.txt"),
        out_dir: Annotated[Path, typer.Option("--out-dir", file_okay=False, dir_okay=True)] = Path(
            "outdir"
        ),
    ) -> None:
        """Exercise every parameter family."""

    nested = typer.Typer()

    @nested.command("inner")
    def inner_command(
        flag: Annotated[bool, typer.Option("--flag")] = False,
    ) -> None:
        """Nested leaf command."""

    app.add_typer(nested, name="group")
    registered = (plain_command, typed_command, inner_command)
    for command_function in registered:
        assert callable(command_function)
    return app


ChoiceMode = Literal["fast", "careful"]


def _choice_app() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)

    @app.command("with-choice")
    def with_choice_command(
        mode: Annotated[ChoiceMode, typer.Option("--mode")] = "fast",
    ) -> None:
        """Command with a fixed choice option."""

    @app.command("other")
    def other_command() -> None:
        """Keep the fixture app a multi-command group."""

    registered = (with_choice_command, other_command)
    for command_function in registered:
        assert callable(command_function)
    return app


# -- schema derivation -------------------------------------------------------


def test_schema_covers_arguments_paths_booleans_defaults() -> None:
    schema = collect_console_schema(_fixture_app())
    assert schema.schema_version == "1"
    assert {command.path for command in schema.commands} == {"plain", "typed", "group inner"}

    plain = schema.command_for("plain")
    argument = next(p for p in plain.parameters if p.parameter == "source")
    assert argument.is_argument
    assert argument.required
    assert argument.kind is ParameterKind.PATH
    assert argument.must_exist
    option = next(p for p in plain.parameters if p.parameter == "note")
    assert not option.is_argument
    assert option.flag_names == ("--note",)
    assert option.default == "default note"
    assert not option.required

    typed = schema.command_for("typed")
    count = next(p for p in typed.parameters if p.parameter == "count")
    assert count.kind is ParameterKind.INTEGER
    assert (count.integer_min, count.integer_max) == (1, 9)
    ratio = next(p for p in typed.parameters if p.parameter == "ratio")
    assert ratio.kind is ParameterKind.NUMBER
    force = next(p for p in typed.parameters if p.parameter == "force")
    assert force.kind is ParameterKind.BOOLEAN
    assert force.boolean_flag_only
    assert force.default == "false"
    dual = next(p for p in typed.parameters if p.parameter == "dual")
    assert dual.kind is ParameterKind.BOOLEAN
    assert dual.boolean_off_flag == "--no-dual"
    assert dual.default == "true"
    out_file = next(p for p in typed.parameters if p.parameter == "out_file")
    assert out_file.kind is ParameterKind.FILE
    out_dir = next(p for p in typed.parameters if p.parameter == "out_dir")
    assert out_dir.kind is ParameterKind.FOLDER


def test_schema_covers_choices() -> None:
    schema = collect_console_schema(_choice_app())
    command = schema.command_for("with-choice")
    mode = next(p for p in command.parameters if p.parameter == "mode")
    assert mode.kind is ParameterKind.CHOICE
    assert set(mode.choices) == {"fast", "careful"}
    assert mode.default == "fast"


def test_real_cli_schema_matches_command_tree() -> None:
    schema = collect_console_schema(cli_app)
    paths = {command.path for command in schema.commands}
    expected = {
        "add",
        "find",
        "ask",
        "report",
        "status",
        "source",
        "backup",
        "export",
        "restore",
        "sample-vault",
        "ingest",
        "search",
        "doctor",
        "version",
        "benchmark-ocr",
        "model configure",
        "model test",
        "model disable",
    }
    assert expected <= paths
    # Completion plumbing must never surface as console parameters.
    for command in schema.commands:
        for parameter in command.parameters:
            assert not set(parameter.flag_names) & {
                "--help",
                "--install-completion",
                "--show-completion",
            }
    # The console itself must not recurse into the console UI command form.
    ui = next(command for command in schema.commands if command.path == "ui")
    assert ui.parameters  # --home
    find = schema.command_for("find")
    json_option = next(p for p in find.parameters if p.parameter == "json_output")
    assert json_option.kind is ParameterKind.BOOLEAN


# -- argv construction ---------------------------------------------------------


def test_build_argv_encodes_declared_parameters() -> None:
    schema = collect_console_schema(_fixture_app())
    typed = schema.command_for("typed")
    argv = build_argv(
        typed,
        {"count": "5", "ratio": 0.25, "force": True, "dual": "false"},
    )
    assert argv[0] == "typed"
    assert "--count" in argv and argv[argv.index("--count") + 1] == "5"
    assert "--ratio" in argv and argv[argv.index("--ratio") + 1] == "0.25"
    assert "--force" in argv
    assert "--no-dual" in argv
    assert "--dual" not in argv


def test_build_argv_uses_defaults_when_untouched() -> None:
    schema = collect_console_schema(_fixture_app())
    typed = schema.command_for("typed")
    argv = build_argv(typed, {})
    assert "--count" not in argv  # matches the CLI default
    assert "--dual" not in argv
    assert "--no-dual" not in argv


def test_build_argv_rejects_unknown_missing_and_out_of_range() -> None:
    schema = collect_console_schema(_fixture_app())
    typed = schema.command_for("typed")
    with pytest.raises(UiError, match="unknown parameter"):
        build_argv(typed, {"definitely_not_a_parameter": "x"})
    with pytest.raises(UiError, match="at least 1"):
        build_argv(typed, {"count": "0"})
    with pytest.raises(UiError, match="integer"):
        build_argv(typed, {"count": "not-a-number"})
    plain = schema.command_for("plain")
    with pytest.raises(UiError, match="missing required"):
        build_argv(plain, {})


def test_build_argv_rejects_undeclared_choice_and_nul_bytes() -> None:
    schema = collect_console_schema(_choice_app())
    command = schema.command_for("with-choice")
    with pytest.raises(UiError, match="must be one of"):
        build_argv(command, {"mode": "fast; rm -rf /"})
    with pytest.raises(UiError, match="NUL"):
        build_argv(command, {"mode": "fast\x00careful"})


def test_command_construction_cannot_inject_shell_commands() -> None:
    schema = collect_console_schema(_fixture_app())
    plain = schema.command_for("plain")
    hostile = "/tmp/$(touch /tmp/archiv-injected); `whoami`; a b"
    argv = build_argv(plain, {"source": hostile, "note": "--force; $(evil)"})
    # Every hostile string remains exactly one argv element, unmodified.
    assert hostile in argv
    assert "--force; $(evil)" in argv
    assert not any(";" in part and part not in {hostile, "--force; $(evil)"} for part in argv)
    for marker in (
        "/tmp/archiv-injected",
        "/tmp/archiv-injected-2",
    ):
        Path(marker).unlink(missing_ok=True)
    outcome = ConsoleRunner()
    outcome.start(
        [
            sys.executable,
            "-c",
            "import sys; print(chr(10).join(sys.argv[1:]))",
            *argv,
        ]
    )
    result = outcome.wait(timeout=30)
    assert result.exit_code == 0
    assert hostile in result.output
    assert not Path("/tmp/archiv-injected").exists()
    assert not Path("/tmp/archiv-injected-2").exists()


def test_quoted_invocation_is_display_only_and_reversible() -> None:
    invocation = quoted_invocation(["find", "--query text", "two words"])
    assert invocation.startswith("archiv find")
    assert "'--query text'" in invocation


# -- runner --------------------------------------------------------------------


def test_runner_runs_argv_shell_free_and_retains_complete_output() -> None:
    runner_instance = ConsoleRunner()
    argv = console_executable_argv(["version"])
    runner_instance.start(argv)
    outcome = runner_instance.wait(timeout=60)
    assert outcome.exit_code == 0
    assert outcome.output.strip()  # exact Archiv version text
    assert outcome.argv == tuple(argv)
    assert not outcome.output_truncated
    assert not runner_instance.busy


def test_runner_captures_exit_status_and_stderr_merged() -> None:
    runner_instance = ConsoleRunner()
    runner_instance.start(
        [sys.executable, "-c", "import sys; print('oops', file=sys.stderr); sys.exit(3)"]
    )
    outcome = runner_instance.wait(timeout=30)
    assert outcome.exit_code == 3
    assert "oops" in outcome.output


def test_runner_allows_only_one_command_at_a_time() -> None:
    runner_instance = ConsoleRunner()
    runner_instance.start([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert runner_instance.busy
        with pytest.raises(UiError, match="still running"):
            runner_instance.start([sys.executable, "-c", "print('no')"])
    finally:
        runner_instance.terminate()
        runner_instance.wait(timeout=30)
    assert not runner_instance.busy


def test_runner_rejects_empty_and_nul_argv() -> None:
    runner_instance = ConsoleRunner()
    with pytest.raises(UiError):
        runner_instance.start([])
    with pytest.raises(UiError):
        runner_instance.start(["echo", "bad\x00value"])


def test_runner_streams_chunks_to_callback() -> None:
    chunks: list[bytes] = []
    done = threading.Event()

    def on_output(chunk: bytes) -> None:
        chunks.append(chunk)
        done.set()

    runner_instance = ConsoleRunner(on_output=on_output)
    runner_instance.start([sys.executable, "-c", "print('streamed')"])
    runner_instance.wait(timeout=30)
    assert done.is_set()
    assert b"streamed" in b"".join(chunks)


def test_runner_terminate_stops_long_runs() -> None:
    runner_instance = ConsoleRunner()
    runner_instance.start([sys.executable, "-c", "import time; time.sleep(60)"])
    start = time.monotonic()
    runner_instance.terminate()
    outcome = runner_instance.wait(timeout=30)
    assert time.monotonic() - start < 15
    assert outcome.exit_code != 0


# -- verified outputs ----------------------------------------------------------


def test_parse_run_json_accepts_only_pure_json() -> None:
    assert parse_run_json('{"a": 1}') == {"a": 1}
    assert parse_run_json("not json") is None
    assert parse_run_json("prefix {} suffix") is None
    assert parse_run_json("") is None


def test_verify_artifact_boundaries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    inside = home / "outputs" / "report.docx"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"docx")
    resolved = verify_artifact(inside, home=home)
    assert resolved == inside.resolve()

    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    with pytest.raises(UiError, match="Archiv-controlled storage"):
        verify_artifact(outside, home=home)
    # An explicit user-entered output path is openable.
    assert verify_artifact(outside, home=home, user_paths=(outside,)) == outside.resolve()

    with pytest.raises(UiError, match="does not exist"):
        verify_artifact(tmp_path / "missing.txt", home=home)

    link = home / "outputs" / "link.docx"
    os.symlink(outside, link)
    with pytest.raises(UiError):
        verify_artifact(link, home=home)


def test_inspect_run_output_extracts_verified_artifacts_and_citations(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    target = home / "originals" / "sha256" / "ab"
    target.mkdir(parents=True)
    payload_file = target / ("ab" * 32)
    payload_file.write_bytes(b"payload")
    citation = {
        "schema_version": "1",
        "segment_id": "1" * 64,
        "segment_index": 0,
        "object_sha256": "ab" * 32,
        "source_name": "decision.txt",
        "media_type": "text/plain",
        "kind": "txt",
        "locator": {"line": 1},
        "normalized_path": "derived/x.txt",
        "normalized_sha256": "2" * 64,
        "text_sha256": "3" * 64,
    }
    output = json.dumps(
        {
            "schema_version": "1",
            "docx_path": str(target / "ghost.docx"),
            "retrieved_citations": [citation],
        }
    )
    result = inspect_run_output(output, home=home)
    assert result.citation_count == 1
    # ghost.docx was never created: not openable, silently excluded.
    assert result.artifacts == []


def test_collect_user_paths_only_takes_absolute_entries() -> None:
    values = {"output": "/tmp/report.docx", "limit": "5", "home": "~/archiv"}
    paths = collect_user_paths(values)
    assert Path("/tmp/report.docx") in paths
    assert Path("~/archiv") in paths
    assert all(str(p).startswith(("/", "~")) for p in paths)


def test_citation_resolution_reuses_bounded_source_validator(tmp_path: Path) -> None:
    """Console citation opening goes through source revalidation end to end."""

    from archiv.ingestion import ingest_file
    from archiv.search import rebuild_search_index, search_documents

    home = tmp_path / "home"
    source = tmp_path / "evidence.txt"
    source.write_text("console citation validation marker\nsecond line\n")
    ingest_file(source, home=home)
    rebuild_search_index(home=home)
    matches = search_documents("console citation validation", home=home)
    assert matches, "search index must return the ingested evidence"
    citation_payload = [match.model_dump(mode="json") for match in matches]

    resolved = resolve_citation_path(citation_payload, citation_number=1, home=home)
    citation = matches[0].citation
    assert resolved.is_file()
    assert sha256_file(resolved) == citation.object_sha256
    # The console path is inside the immutable originals store.
    layout_root = (home / "originals").resolve()
    assert resolved.resolve().is_relative_to(layout_root)

    tampered = json.loads(json.dumps(citation_payload))
    tampered[0]["citation"]["object_sha256"] = "f" * 64
    with pytest.raises((UiError, ValueError)):
        resolve_citation_path(tampered, citation_number=1, home=home)

    with pytest.raises((UiError, ValueError)):
        resolve_citation_path(citation_payload, citation_number=99, home=home)


# -- desktop handler -----------------------------------------------------------


def _no_handler(_name: str) -> None:
    return None


def test_open_with_default_handler_requires_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("content")
    monkeypatch.setattr(shutil, "which", _no_handler)
    with pytest.raises(UiError, match="no default file handler"):
        open_with_default_handler(target)


def test_open_with_default_handler_invokes_argv_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("content")
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("CALLS", str(calls))
    script = tmp_path / "record-opener"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> {calls}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def recorded_handler(name: str) -> str | None:
        return str(script) if name == "xdg-open" else None

    monkeypatch.setattr(shutil, "which", recorded_handler)
    open_with_default_handler(target)
    assert calls.read_text().strip() == str(target.resolve())


def test_open_with_default_handler_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("content")
    failing = tmp_path / "failing-opener"
    failing.write_text("#!/bin/sh\necho 'cannot open' >&2\nexit 2\n")
    failing.chmod(failing.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def failing_handler(name: str) -> str | None:
        return str(failing) if name == "xdg-open" else None

    monkeypatch.setattr(shutil, "which", failing_handler)
    with pytest.raises(UiError, match="failed"):
        open_with_default_handler(target)


# -- form plan -------------------------------------------------------------------


def test_control_plan_maps_kinds_to_controls() -> None:
    schema = collect_console_schema(_fixture_app())
    typed = schema.command_for("typed")
    plans = {plan.parameter.parameter: plan for plan in control_plan(typed)}
    assert plans["force"].is_checkbox
    assert not plans["force"].initial_checked
    assert plans["dual"].initial_checked
    assert plans["out_file"].browse is BrowseMode.FILE
    assert plans["out_dir"].browse is BrowseMode.FOLDER
    assert plans["count"].spinbox_min == 1 and plans["count"].spinbox_max == 9
    assert plans["out_file"].initial_text == "out.txt"
    schema_choice = collect_console_schema(_choice_app())
    choice_plan = control_plan(schema_choice.command_for("with-choice"))[0]
    assert choice_plan.is_choice
    assert set(choice_plan.choices) == {"fast", "careful"}

    plain_plan = control_plan(schema.command_for("plain"))
    source_plan = next(plan for plan in plain_plan if plan.parameter.parameter == "source")
    assert source_plan.browse is BrowseMode.FILE_OR_FOLDER
    assert source_plan.required


# -- console CLI behaviour --------------------------------------------------------


def test_ui_command_fails_clearly_without_desktop_support() -> None:
    """Without tkinter or a display the command must fail with guidance."""

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET", "XDG_SESSION_TYPE"}
    }
    result = subprocess.run(
        [sys.executable, "-m", "archiv.cli", "ui"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 1
    assert "python3-tkinter" in result.stderr or "display" in result.stderr.lower()


def test_ui_command_reports_missing_tkinter_c_extension() -> None:
    """A missing ``_tkinter`` extension must guide, not traceback.

    Importing ``tkinter`` raises ``ModuleNotFoundError`` naming the pure Python
    package when it is absent, but naming ``_tkinter`` when only the compiled
    extension is missing.  The latter is the common real-world case (Debian
    without ``python3-tk``, source builds without Tcl/Tk headers), so both
    names must produce the same actionable message.
    """

    for missing in ("tkinter", "_tkinter"):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import builtins, sys\n"
                    f"missing = {missing!r}\n"
                    "real_import = builtins.__import__\n"
                    "def fake_import(name, *args, **kwargs):\n"
                    "    if name == 'tkinter' or name.startswith('tkinter.'):\n"
                    "        raise ModuleNotFoundError(\n"
                    "            f'No module named {missing!r}', name=missing\n"
                    "        )\n"
                    "    return real_import(name, *args, **kwargs)\n"
                    "builtins.__import__ = fake_import\n"
                    "for cached in [k for k in sys.modules if k.startswith('tkinter')]:\n"
                    "    del sys.modules[cached]\n"
                    "from archiv.cli import app\n"
                    "app()\n"
                ),
                "ui",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1, result.stderr
        assert "python3-tkinter" in result.stderr, result.stderr
        assert "Traceback" not in result.stderr, result.stderr


def test_ui_command_is_registered_and_cli_help_unchanged() -> None:
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "ui" in result.output
    # Existing everyday commands still exist and keep their contracts.
    for name in ("add", "find", "ask", "report", "status", "source", "backup"):
        assert name in result.output


def test_installer_includes_minimal_desktop_packages() -> None:
    script = Path(__file__).parents[1] / "tools" / "install-fedora.sh"
    content = script.read_text(encoding="utf-8")
    assert "python3-tkinter" in content
    assert "xdg-utils" in content
