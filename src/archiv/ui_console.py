"""Minimal local test console over Archiv's existing Typer commands."""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import typer
from typer.core import TyperGroup, TyperOption
from typer.main import get_command

from archiv.source_location import load_citation_file, resolve_citation_location

ControlKind = Literal["text", "integer", "float", "boolean", "choice", "path"]
PathKind = Literal["file", "directory", "any"]


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One CLI parameter rendered by the test console."""

    name: str
    label: str
    parameter_kind: Literal["argument", "option"]
    control_kind: ControlKind
    required: bool
    help: str
    default: object
    option_flag: str | None
    negative_flag: str | None
    choices: tuple[str, ...]
    path_kind: PathKind | None
    multiple: bool


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One runnable leaf command from the real Typer command tree."""

    path: tuple[str, ...]
    help: str
    parameters: tuple[ParameterSpec, ...]

    @property
    def display_name(self) -> str:
        return " ".join(self.path)


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Validated argv construction result."""

    command: CommandSpec
    argv: tuple[str, ...]

    @property
    def equivalent_cli(self) -> str:
        return shlex.join(("archiv", *self.argv))


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    """Inspectable files and citations found in one successful JSON result."""

    result_file: Path | None
    paths: tuple[Path, ...]
    citation_count: int


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _option_flag(parameter: TyperOption) -> str | None:
    for flag in parameter.opts:
        if flag.startswith("--"):
            return flag
    return parameter.opts[0] if parameter.opts else None


def _negative_flag(parameter: TyperOption) -> str | None:
    for flag in parameter.secondary_opts:
        if flag.startswith("--"):
            return flag
    return parameter.secondary_opts[0] if parameter.secondary_opts else None


def _path_kind(parameter_type: Any) -> PathKind | None:
    file_okay = getattr(parameter_type, "file_okay", None)
    dir_okay = getattr(parameter_type, "dir_okay", None)
    if not isinstance(file_okay, bool) or not isinstance(dir_okay, bool):
        return None
    if file_okay and dir_okay:
        return "any"
    if file_okay:
        return "file"
    return "directory"


def _control_kind(parameter: Any) -> ControlKind:
    parameter_type = parameter.type
    if isinstance(parameter, TyperOption) and bool(parameter.is_flag):
        return "boolean"
    if hasattr(parameter_type, "choices"):
        return "choice"
    if _path_kind(parameter_type) is not None:
        return "path"
    type_name = str(getattr(parameter_type, "name", "text"))
    if type_name.startswith("integer"):
        return "integer"
    if type_name.startswith("float"):
        return "float"
    return "text"


def _serializable_default(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value)
    return str(value)


def _parameter_spec(parameter: Any) -> ParameterSpec:
    parameter_kind: Literal["argument", "option"]
    option_flag: str | None = None
    negative_flag: str | None = None
    help_text = cast(str | None, getattr(parameter, "help", None)) or ""
    choices: tuple[str, ...] = ()
    raw_choices = getattr(parameter.type, "choices", ())
    if isinstance(raw_choices, Sequence) and not isinstance(
        raw_choices, (str, bytes, bytearray)
    ):
        choices = tuple(str(choice) for choice in raw_choices)

    if isinstance(parameter, TyperOption):
        parameter_kind = "option"
        option_flag = _option_flag(parameter)
        negative_flag = _negative_flag(parameter)
    else:
        parameter_kind = "argument"

    return ParameterSpec(
        name=parameter.name or "value",
        label=_humanize(parameter.name or "value"),
        parameter_kind=parameter_kind,
        control_kind=_control_kind(parameter),
        required=parameter.required,
        help=help_text,
        default=_serializable_default(parameter.default),
        option_flag=option_flag,
        negative_flag=negative_flag,
        choices=choices,
        path_kind=_path_kind(parameter.type),
        multiple=parameter.multiple or parameter.nargs != 1,
    )


def build_command_catalog(app: typer.Typer) -> tuple[CommandSpec, ...]:
    """Build a deterministic UI catalog from the actual Typer command tree."""

    root = get_command(app)
    commands: list[CommandSpec] = []

    def visit(command: Any, path: tuple[str, ...]) -> None:
        if isinstance(command, TyperGroup) or hasattr(command, "commands"):
            children = cast(Mapping[str, Any], command.commands)
            for name, child in sorted(children.items()):
                if name == "ui" or bool(getattr(child, "hidden", False)):
                    continue
                visit(child, (*path, name))
            return
        commands.append(
            CommandSpec(
                path=path,
                help=(command.help or command.short_help or "").strip(),
                parameters=tuple(_parameter_spec(parameter) for parameter in command.params),
            )
        )

    if isinstance(root, TyperGroup) or hasattr(root, "commands"):
        visit(root, ())
    else:
        command_name = root.name or "archiv"
        if command_name != "ui":
            visit(root, (command_name,))
    return tuple(sorted(commands, key=lambda item: item.path))


def _split_multiple(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if not text:
        return ()
    return tuple(part.strip() for part in text.replace("\n", ",").split(",") if part.strip())


def _as_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def build_run_request(command: CommandSpec, values: Mapping[str, object]) -> RunRequest:
    """Convert UI values into a shell-free argument vector for one known command."""

    argv: list[str] = list(command.path)
    arguments: list[str] = []
    options: list[str] = []

    for parameter in command.parameters:
        supplied = values.get(parameter.name, parameter.default)
        if parameter.control_kind == "boolean":
            selected = bool(supplied)
            default = bool(parameter.default)
            if selected != default:
                selected_flag = parameter.option_flag if selected else parameter.negative_flag
                if selected_flag is None:
                    raise ValueError(f"{parameter.label} cannot be toggled from its default")
                options.append(selected_flag)
            continue

        items = _split_multiple(supplied) if parameter.multiple else (_as_text(supplied),)
        items = tuple(item for item in items if item)
        if parameter.required and not items:
            raise ValueError(f"{parameter.label} is required")
        if not items:
            continue
        if parameter.control_kind == "choice":
            invalid = [item for item in items if item not in parameter.choices]
            if invalid:
                raise ValueError(f"{parameter.label} has an unsupported value: {invalid[0]}")
        if parameter.control_kind == "integer":
            for item in items:
                int(item)
        if parameter.control_kind == "float":
            for item in items:
                float(item)

        if parameter.parameter_kind == "argument":
            arguments.extend(items)
            continue
        if parameter.option_flag is None:
            raise ValueError(f"{parameter.label} has no usable command-line flag")
        for item in items:
            options.extend((parameter.option_flag, item))

    argv.extend(arguments)
    argv.extend(options)
    return RunRequest(command=command, argv=tuple(argv))


_PATH_KEYS = {
    "archive_path",
    "backup_path",
    "canonical_path",
    "evidence_dir",
    "evidence_path",
    "file_path",
    "output_dir",
    "output_path",
    "path",
    "rendered_path",
    "report_path",
    "source_path",
}


def _path_values(value: object, *, key: str | None = None) -> Iterable[str]:
    normalized_key = key.lower() if key is not None else ""
    key_is_path = (
        normalized_key in _PATH_KEYS
        or normalized_key.endswith("_path")
        or normalized_key.endswith("_file")
        or normalized_key.endswith("_dir")
    )
    if isinstance(value, str):
        if key_is_path:
            yield value
        return
    if isinstance(value, Mapping):
        for child_key, item in value.items():
            yield from _path_values(item, key=str(child_key))
        return
    if key_is_path and isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            if isinstance(item, str):
                yield item


def _existing_absolute_paths(payload: object) -> tuple[Path, ...]:
    found: set[Path] = set()
    for value in _path_values(payload):
        try:
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        found.add(resolved)
    return tuple(sorted(found, key=str))


def _citation_count(result_file: Path) -> int:
    count = 0
    for citation_number in range(1, 101):
        try:
            load_citation_file(result_file, citation_number=citation_number)
        except ValueError:
            break
        count += 1
    return count


def inspect_run_output(output: str, *, state_root: Path | None = None) -> RunArtifacts:
    """Persist valid JSON output and identify only real local paths and citations."""

    try:
        payload: object = json.loads(output)
    except json.JSONDecodeError:
        return RunArtifacts(result_file=None, paths=(), citation_count=0)

    root = state_root or Path.home() / ".local" / "state" / "archiv" / "ui-runs"
    run_dir = root / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    result_file = run_dir / "result.json"
    result_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result_file.chmod(0o600)
    return RunArtifacts(
        result_file=result_file,
        paths=_existing_absolute_paths(payload),
        citation_count=_citation_count(result_file),
    )


def open_with_default_handler(path: Path) -> None:
    """Open an existing local file or directory without invoking a shell."""

    resolved = path.expanduser().resolve(strict=True)
    if os.name == "nt":
        startfile = cast(Callable[[str], object] | None, getattr(os, "startfile", None))
        if startfile is None:
            raise RuntimeError("Windows default file handler is unavailable")
        startfile(str(resolved))
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    executable = shutil.which(opener)
    if executable is None:
        raise RuntimeError(f"default file handler command is unavailable: {opener}")
    subprocess.Popen(
        [executable, str(resolved)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def resolve_and_open_citation(
    result_file: Path,
    citation_number: int,
    *,
    home: Path | None = None,
) -> Path:
    """Validate one citation through Archiv before opening its immutable source."""

    citation = load_citation_file(result_file, citation_number=citation_number)
    location = resolve_citation_location(citation, home=home)
    source_path = Path(location.canonical_path).resolve(strict=True)
    open_with_default_handler(source_path)
    return source_path


class _UiUnavailable(RuntimeError):
    pass


def _load_tk() -> tuple[Any, Any, Any, Any]:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as error:
        raise _UiUnavailable(
            "Archiv Test Console requires Tk. On Fedora, install python3-tkinter."
        ) from error
    return tk, ttk, filedialog, messagebox


class ArchivTestConsole:
    """Small Tk wrapper around the existing Archiv CLI process."""

    def __init__(self, app: typer.Typer) -> None:
        self._tk, self._ttk, self._filedialog, self._messagebox = _load_tk()
        self._catalog = build_command_catalog(app)
        if not self._catalog:
            raise _UiUnavailable("Archiv exposes no runnable commands")
        self._by_name = {command.display_name: command for command in self._catalog}
        self._root = self._tk.Tk()
        self._root.title("Archiv Test Console")
        self._root.geometry("980x760")
        self._root.minsize(760, 600)
        self._event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="archiv-ui-")
        self._process: subprocess.Popen[str] | None = None
        self._output_parts: list[str] = []
        self._artifacts = RunArtifacts(result_file=None, paths=(), citation_count=0)
        self._variables: dict[str, Any] = {}
        self._parameter_widgets: list[Any] = []
        self._command_var = self._tk.StringVar(value=self._catalog[0].display_name)
        self._status_var = self._tk.StringVar(value="Ready")
        self._cli_var = self._tk.StringVar(value="")
        self._build_window()
        self._render_parameters()
        self._root.after(100, self._poll_events)
        self._root.protocol("WM_DELETE_WINDOW", self._close)

    def run(self) -> None:
        self._root.mainloop()

    def _build_window(self) -> None:
        outer = self._ttk.Frame(self._root, padding=12)
        outer.pack(fill="both", expand=True)

        selection = self._ttk.LabelFrame(outer, text="Command", padding=10)
        selection.pack(fill="x")
        command_box = self._ttk.Combobox(
            selection,
            textvariable=self._command_var,
            values=[command.display_name for command in self._catalog],
            state="readonly",
        )
        command_box.pack(fill="x")
        command_box.bind("<<ComboboxSelected>>", lambda _event: self._render_parameters())

        self._help_label = self._ttk.Label(selection, text="", wraplength=900)
        self._help_label.pack(fill="x", pady=(8, 0))

        self._parameters = self._ttk.LabelFrame(outer, text="Arguments", padding=10)
        self._parameters.pack(fill="x", pady=(10, 0))

        controls = self._ttk.Frame(outer)
        controls.pack(fill="x", pady=(10, 0))
        self._run_button = self._ttk.Button(controls, text="Run", command=self._start_run)
        self._run_button.pack(side="left")
        self._cancel_button = self._ttk.Button(
            controls,
            text="Cancel",
            command=self._cancel_run,
            state="disabled",
        )
        self._cancel_button.pack(side="left", padx=(8, 0))
        self._progress = self._ttk.Progressbar(controls, mode="indeterminate", length=220)
        self._progress.pack(side="right")
        self._ttk.Label(controls, textvariable=self._status_var).pack(side="right", padx=(0, 12))

        invocation = self._ttk.LabelFrame(outer, text="Equivalent CLI invocation", padding=8)
        invocation.pack(fill="x", pady=(10, 0))
        cli_entry = self._ttk.Entry(invocation, textvariable=self._cli_var, state="readonly")
        cli_entry.pack(fill="x")

        output_frame = self._ttk.LabelFrame(outer, text="Output", padding=8)
        output_frame.pack(fill="both", expand=True, pady=(10, 0))
        self._output = self._tk.Text(output_frame, wrap="none", height=18)
        output_scroll = self._ttk.Scrollbar(output_frame, command=self._output.yview)
        self._output.configure(yscrollcommand=output_scroll.set)
        self._output.pack(side="left", fill="both", expand=True)
        output_scroll.pack(side="right", fill="y")

        self._open_frame = self._ttk.LabelFrame(outer, text="Open verified results", padding=8)
        self._open_frame.pack(fill="x", pady=(10, 0))
        self._ttk.Label(
            self._open_frame,
            text="Successful JSON output paths and validated citations appear here.",
        ).pack(anchor="w")

    def _selected_command(self) -> CommandSpec:
        return self._by_name[self._command_var.get()]

    def _clear_parameter_widgets(self) -> None:
        for widget in self._parameter_widgets:
            widget.destroy()
        self._parameter_widgets.clear()
        self._variables.clear()

    def _render_parameters(self) -> None:
        self._clear_parameter_widgets()
        command = self._selected_command()
        self._help_label.configure(text=command.help or "No additional command description.")
        for row, parameter in enumerate(command.parameters):
            label = self._ttk.Label(
                self._parameters,
                text=parameter.label + (" *" if parameter.required else ""),
            )
            label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            self._parameter_widgets.append(label)

            if parameter.control_kind == "boolean":
                variable = self._tk.BooleanVar(value=bool(parameter.default))
                widget = self._ttk.Checkbutton(self._parameters, variable=variable)
            else:
                if parameter.default is None:
                    default = ""
                elif isinstance(parameter.default, Sequence) and not isinstance(
                    parameter.default, (str, bytes, bytearray)
                ):
                    default = ", ".join(str(item) for item in parameter.default)
                else:
                    default = str(parameter.default)
                variable = self._tk.StringVar(value=default)
                if parameter.control_kind == "choice":
                    widget = self._ttk.Combobox(
                        self._parameters,
                        textvariable=variable,
                        values=parameter.choices,
                        state="readonly",
                    )
                else:
                    widget = self._ttk.Entry(self._parameters, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self._parameter_widgets.append(widget)
            self._variables[parameter.name] = variable

            if parameter.control_kind == "path":
                browse_frame = self._ttk.Frame(self._parameters)
                browse_frame.grid(row=row, column=2, padx=(8, 0), pady=4)
                self._parameter_widgets.append(browse_frame)
                if parameter.path_kind == "any":
                    self._ttk.Button(
                        browse_frame,
                        text="File…",
                        command=lambda item=parameter: self._browse(item, directory=False),
                    ).pack(side="left")
                    self._ttk.Button(
                        browse_frame,
                        text="Folder…",
                        command=lambda item=parameter: self._browse(item, directory=True),
                    ).pack(side="left", padx=(4, 0))
                else:
                    self._ttk.Button(
                        browse_frame,
                        text="Browse…",
                        command=lambda item=parameter: self._browse(
                            item, directory=item.path_kind == "directory"
                        ),
                    ).pack(side="left")

            if parameter.help:
                help_label = self._ttk.Label(
                    self._parameters,
                    text=parameter.help,
                    foreground="#555555",
                    wraplength=420,
                )
                help_label.grid(row=row, column=3, sticky="w", padx=(10, 0), pady=4)
                self._parameter_widgets.append(help_label)

        self._parameters.columnconfigure(1, weight=1)
        self._refresh_cli_preview()
        for variable in self._variables.values():
            variable.trace_add("write", lambda *_args: self._refresh_cli_preview())

    def _browse(self, parameter: ParameterSpec, *, directory: bool) -> None:
        selected = ""
        if directory:
            selected = cast(str, self._filedialog.askdirectory())
        else:
            selected = cast(str, self._filedialog.askopenfilename())
        if selected:
            self._variables[parameter.name].set(selected)

    def _current_values(self) -> dict[str, object]:
        return {name: variable.get() for name, variable in self._variables.items()}

    def _refresh_cli_preview(self) -> None:
        try:
            request = build_run_request(self._selected_command(), self._current_values())
        except (TypeError, ValueError):
            self._cli_var.set("Complete the required fields to preview the command.")
            return
        self._cli_var.set(request.equivalent_cli)

    def _start_run(self) -> None:
        if self._process is not None:
            return
        try:
            request = build_run_request(self._selected_command(), self._current_values())
        except (TypeError, ValueError) as error:
            self._messagebox.showerror("Cannot run command", str(error))
            return
        self._output.delete("1.0", "end")
        self._output_parts.clear()
        self._artifacts = RunArtifacts(result_file=None, paths=(), citation_count=0)
        self._render_open_actions()
        self._cli_var.set(request.equivalent_cli)
        self._status_var.set(f"Running {request.command.display_name}")
        self._run_button.configure(state="disabled")
        self._cancel_button.configure(state="normal")
        self._progress.start(12)
        worker = threading.Thread(target=self._run_worker, args=(request,), daemon=True)
        worker.start()

    def _run_worker(self, request: RunRequest) -> None:
        command = [sys.executable, "-m", "archiv.cli", *request.argv]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
            )
            self._event_queue.put(("process", process))
            assert process.stdout is not None
            for line in process.stdout:
                self._event_queue.put(("output", line))
            return_code = process.wait()
            self._event_queue.put(("finished", return_code))
        except OSError as error:
            self._event_queue.put(("failed", f"Could not start Archiv: {error}"))

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "process":
                self._process = cast(subprocess.Popen[str], payload)
            elif event == "output":
                text = str(payload)
                self._output_parts.append(text)
                self._output.insert("end", text)
                self._output.see("end")
            elif event == "finished":
                self._finish_run(int(cast(int, payload)))
            elif event == "failed":
                self._output.insert("end", str(payload) + "\n")
                self._finish_run(1)
        self._root.after(100, self._poll_events)

    def _finish_run(self, return_code: int) -> None:
        self._process = None
        self._progress.stop()
        self._run_button.configure(state="normal")
        self._cancel_button.configure(state="disabled")
        if return_code == 0:
            self._status_var.set("Succeeded")
            self._artifacts = inspect_run_output(
                "".join(self._output_parts),
                state_root=Path(self._temp_dir.name),
            )
        else:
            self._status_var.set(f"Failed with exit code {return_code}")
        self._render_open_actions()

    def _cancel_run(self) -> None:
        process = self._process
        if process is None:
            return
        self._status_var.set("Cancelling…")
        process.terminate()

    def _render_open_actions(self) -> None:
        for widget in self._open_frame.winfo_children():
            widget.destroy()
        if not self._artifacts.paths and self._artifacts.citation_count == 0:
            self._ttk.Label(
                self._open_frame,
                text="Successful JSON output paths and validated citations appear here.",
            ).pack(anchor="w")
            return
        for path in self._artifacts.paths:
            self._ttk.Button(
                self._open_frame,
                text=f"Open {path.name or path}",
                command=lambda item=path: self._open_path(item),
            ).pack(side="left", padx=(0, 8), pady=2)
        if self._artifacts.result_file is not None:
            for citation_number in range(1, self._artifacts.citation_count + 1):
                self._ttk.Button(
                    self._open_frame,
                    text=f"Open citation {citation_number}",
                    command=lambda number=citation_number: self._open_citation(number),
                ).pack(side="left", padx=(0, 8), pady=2)

    def _open_path(self, path: Path) -> None:
        try:
            open_with_default_handler(path)
        except (OSError, RuntimeError, ValueError) as error:
            self._messagebox.showerror("Could not open result", str(error))

    def _selected_home(self) -> Path | None:
        variable = self._variables.get("home")
        if variable is None:
            return None
        value = str(variable.get()).strip()
        return Path(value).expanduser() if value else None

    def _open_citation(self, citation_number: int) -> None:
        result_file = self._artifacts.result_file
        if result_file is None:
            return
        try:
            resolve_and_open_citation(
                result_file,
                citation_number,
                home=self._selected_home(),
            )
        except (OSError, RuntimeError, ValueError) as error:
            self._messagebox.showerror("Could not open citation", str(error))

    def _close(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        self._root.destroy()
        self._temp_dir.cleanup()


def launch_test_console(app: typer.Typer) -> None:
    """Launch the native local test console or fail with a useful message."""

    try:
        console = ArchivTestConsole(app)
        console.run()
    except _UiUnavailable as error:
        raise RuntimeError(str(error)) from error


def register_ui_command(app: typer.Typer) -> Callable[..., None]:
    """Attach the `archiv ui` command after all product commands are registered."""

    @app.command("ui")
    def ui_command() -> None:
        """Open the minimal local test console for all public Archiv commands."""

        try:
            launch_test_console(app)
        except RuntimeError as error:
            typer.echo(f"ui failed: {error}", err=True)
            raise typer.Exit(code=1) from error

    return ui_command
