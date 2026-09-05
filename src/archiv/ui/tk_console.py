"""Tkinter rendering of the Archiv local test console.

This module contains only widget wiring.  Command discovery, argument
validation, process execution, output retention, artifact verification, and
citation resolution all live in sibling modules and are tested without a
display.  Importing this module requires the ``tkinter`` desktop package;
callers must convert a missing dependency or display into a clear failure.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import TclError, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import cast

import typer

from archiv.ui.argv import build_argv, quoted_invocation
from archiv.ui.console_schema import ConsoleCommand, ConsoleSchema, collect_console_schema
from archiv.ui.desktop import open_with_default_handler
from archiv.ui.errors import UiError
from archiv.ui.form_plan import BrowseMode, ControlPlan, control_plan
from archiv.ui.outputs import (
    RunOutputs,
    collect_user_paths,
    inspect_run_output,
    resolve_citation_path,
)
from archiv.ui.runner import ConsoleRunner, console_executable_argv

_POLL_MILLISECONDS = 100


class ConsoleApp:
    """One-window test console over the real Archiv command tree."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        schema: ConsoleSchema,
        home: Path | None,
        runner: ConsoleRunner | None = None,
    ) -> None:
        self._root = root
        self._schema = schema
        self._home = home
        self._commands = {command.path: command for command in schema.commands}
        self._plans: list[ControlPlan] = []
        self._entries: dict[str, tk.Variable] = {}
        self._outputs = RunOutputs(run_json=None)
        self._form_values: dict[str, object] = {}
        self._busy = False
        self._runner = runner or ConsoleRunner(on_output=self._append_output)

        root.title("Archiv local test console")
        root.minsize(760, 560)

        header = ttk.Frame(root, padding=8)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Command:").pack(side=tk.LEFT)
        self._command_var = tk.StringVar()
        self._command_box = ttk.Combobox(
            header,
            textvariable=self._command_var,
            state="readonly",
            values=[command.path for command in schema.commands],
            width=40,
        )
        self._command_box.pack(side=tk.LEFT, padx=(4, 8))
        self._command_box.bind("<<ComboboxSelected>>", lambda _event: self._select_command())
        self._summary_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self._summary_var, wraplength=360).pack(side=tk.LEFT)

        self._form = ttk.LabelFrame(root, text="Arguments", padding=8)
        self._form.pack(fill=tk.X, padx=8, pady=(0, 4))

        action = ttk.Frame(root, padding=(8, 0))
        action.pack(fill=tk.X)
        self._run_button = ttk.Button(action, text="Run", command=self._run_selected)
        self._run_button.pack(side=tk.LEFT)
        self._stop_button = ttk.Button(
            action, text="Stop", command=self._stop_run, state=tk.DISABLED
        )
        self._stop_button.pack(side=tk.LEFT, padx=(4, 8))
        self._progress = ttk.Progressbar(action, mode="indeterminate", length=180)
        self._progress.pack(side=tk.LEFT)
        self._status_var = tk.StringVar(value="Idle")
        ttk.Label(action, textvariable=self._status_var).pack(side=tk.LEFT, padx=(8, 0))

        invocation_frame = ttk.Frame(root, padding=(8, 4, 8, 0))
        invocation_frame.pack(fill=tk.X)
        ttk.Label(invocation_frame, text="Equivalent CLI:").pack(side=tk.LEFT)
        self._invocation_var = tk.StringVar(value="")
        self._invocation = ttk.Entry(
            invocation_frame, textvariable=self._invocation_var, state="readonly"
        )
        self._invocation.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        self._output = ScrolledText(root, state=tk.DISABLED, wrap=tk.WORD, height=18)
        self._output.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        opener_frame = ttk.Frame(root, padding=8)
        opener_frame.pack(fill=tk.X)
        ttk.Label(opener_frame, text="Verified output:").pack(side=tk.LEFT)
        self._artifact_var = tk.StringVar()
        self._artifact_box = ttk.Combobox(
            opener_frame, textvariable=self._artifact_var, state="readonly", width=52
        )
        self._artifact_box.pack(side=tk.LEFT, padx=(4, 4))
        self._open_output_button = ttk.Button(
            opener_frame,
            text="Open output",
            command=self._open_output,
            state=tk.DISABLED,
        )
        self._open_output_button.pack(side=tk.LEFT)
        ttk.Label(opener_frame, text="  Citation #:").pack(side=tk.LEFT, padx=(12, 0))
        self._citation_var = tk.StringVar(value="1")
        self._citation_spin = ttk.Spinbox(
            opener_frame, from_=1, to=1, textvariable=self._citation_var, width=5
        )
        self._citation_spin.pack(side=tk.LEFT, padx=(4, 4))
        self._open_citation_button = ttk.Button(
            opener_frame,
            text="Open preserved source",
            command=self._open_citation,
            state=tk.DISABLED,
        )
        self._open_citation_button.pack(side=tk.LEFT)

        if self._schema.commands:
            first = self._schema.commands[0].path
            self._command_var.set(first)
            self._select_command()

    # -- form rendering -----------------------------------------------------

    def _select_command(self) -> None:
        command = self._current_command()
        self._summary_var.set(command.summary)
        for child in self._form.winfo_children():
            child.destroy()
        self._plans = control_plan(command)
        self._entries = {}
        if not self._plans:
            ttk.Label(self._form, text="This command takes no arguments.").pack(anchor=tk.W)
        for plan in self._plans:
            row = ttk.Frame(self._form)
            row.pack(fill=tk.X, pady=1)
            label_text = plan.label + (" *" if plan.required else "")
            ttk.Label(row, text=label_text, width=26).pack(side=tk.LEFT)
            if plan.is_checkbox:
                variable = tk.BooleanVar(value=plan.initial_checked)
                self._entries[plan.parameter.parameter] = variable
                ttk.Checkbutton(row, variable=variable, command=self._update_invocation).pack(
                    side=tk.LEFT
                )
            elif plan.is_choice:
                variable = tk.StringVar(value=plan.initial_text)
                self._entries[plan.parameter.parameter] = variable
                box = ttk.Combobox(
                    row,
                    textvariable=variable,
                    state="readonly",
                    values=list(plan.choices),
                    width=36,
                )
                box.pack(side=tk.LEFT)
                box.bind("<<ComboboxSelected>>", lambda _event: self._update_invocation())
            else:
                variable = tk.StringVar(value=plan.initial_text)
                self._entries[plan.parameter.parameter] = variable
                entry = ttk.Entry(row, textvariable=variable, width=44)
                entry.pack(side=tk.LEFT)
                variable.trace_add("write", self._refresh_on_write)
                self._add_browse_buttons(row, plan, variable)
            if plan.parameter.help:
                ttk.Label(row, text=plan.parameter.help, foreground="#555555", wraplength=280).pack(
                    side=tk.LEFT, padx=(6, 0)
                )
        self._update_invocation()

    def _add_browse_buttons(self, row: ttk.Frame, plan: ControlPlan, variable: tk.Variable) -> None:
        if plan.browse in (BrowseMode.FILE, BrowseMode.FILE_OR_FOLDER):

            def pick_file(var: tk.Variable = variable) -> None:
                chosen = filedialog.askopenfilename()
                if chosen and isinstance(var, tk.StringVar):
                    var.set(chosen)

            ttk.Button(row, text="File…", width=8, command=pick_file).pack(
                side=tk.LEFT, padx=(4, 0)
            )
        if plan.browse in (BrowseMode.FOLDER, BrowseMode.FILE_OR_FOLDER):

            def pick_folder(var: tk.Variable = variable) -> None:
                chosen = filedialog.askdirectory()
                if chosen and isinstance(var, tk.StringVar):
                    var.set(chosen)

            ttk.Button(row, text="Folder…", width=8, command=pick_folder).pack(
                side=tk.LEFT, padx=(4, 0)
            )

    # -- run lifecycle --------------------------------------------------------

    def _refresh_on_write(self, *_args: object) -> None:
        self._update_invocation()

    def _current_command(self) -> ConsoleCommand:
        return self._commands[self._command_var.get()]

    def _collect_values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for plan in self._plans:
            variable = self._entries.get(plan.parameter.parameter)
            if variable is None:
                continue
            value = cast(object, variable.get())  # pyright: ignore[reportUnknownMemberType]
            if isinstance(value, str) and not value.strip():
                continue
            values[plan.parameter.parameter] = value
        return values

    def _update_invocation(self) -> None:
        if self._busy:
            return
        try:
            argv = build_argv(self._current_command(), self._collect_values())
            self._invocation_var.set(quoted_invocation(argv))
        except (UiError, KeyError) as error:
            self._invocation_var.set(f"incomplete: {error}")

    def _append_output(self, chunk: bytes) -> None:
        text = chunk.decode("utf-8", errors="replace")
        self._root.after(0, self._append_output_text, text)

    def _append_output_text(self, text: str) -> None:
        self._output.configure(state=tk.NORMAL)
        self._output.insert(tk.END, text)
        self._output.see(tk.END)
        self._output.configure(state=tk.DISABLED)

    def _run_selected(self) -> None:
        try:
            argv = build_argv(self._current_command(), self._collect_values())
        except (UiError, KeyError) as error:
            messagebox.showerror("Cannot run command", str(error), parent=self._root)
            return
        self._form_values = self._collect_values()
        self._output.configure(state=tk.NORMAL)
        self._output.delete("1.0", tk.END)
        self._output.configure(state=tk.DISABLED)
        full_argv = console_executable_argv(argv)
        try:
            self._runner.start(full_argv)
        except UiError as error:
            messagebox.showerror("Cannot run command", str(error), parent=self._root)
            return
        self._busy = True
        self._run_button.configure(state=tk.DISABLED)
        self._stop_button.configure(state=tk.NORMAL)
        self._progress.start(80)
        self._status_var.set(f"Running: {self._command_var.get()}")
        self._invocation_var.set(quoted_invocation(argv))
        self._root.after(_POLL_MILLISECONDS, self._poll_run)

    def _poll_run(self) -> None:
        if self._runner.busy:
            self._root.after(_POLL_MILLISECONDS, self._poll_run)
            return
        self._finish_run()

    def _finish_run(self) -> None:
        try:
            outcome = self._runner.wait(timeout=0)
        except UiError as error:
            self._status_var.set(f"Run state error: {error}")
            self._busy = False
            self._run_button.configure(state=tk.NORMAL)
            self._stop_button.configure(state=tk.DISABLED)
            self._progress.stop()
            return
        self._busy = False
        self._run_button.configure(state=tk.NORMAL)
        self._stop_button.configure(state=tk.DISABLED)
        self._progress.stop()
        state = "succeeded" if outcome.succeeded else f"failed (exit {outcome.exit_code})"
        self._outputs = inspect_run_output(
            outcome.output,
            home=self._home,
            user_paths=collect_user_paths(self._form_values),
        )
        # Say it in the status line, where the operator is already looking, rather than
        # leaving a remote answer looking identical to a local one.
        if self._outputs.model_provenance == "remote-evaluation":
            self._status_var.set(
                f"Finished: {state} — NOT A LOCAL ANSWER: this archive is in evaluation "
                "mode and its sources were sent to a service you do not control"
            )
        else:
            self._status_var.set(f"Finished: {state}")
        artifact_labels = [
            f"{artifact.origin}: {artifact.path}" for artifact in self._outputs.artifacts
        ]
        self._artifact_box.configure(values=artifact_labels)
        if artifact_labels:
            self._artifact_var.set(artifact_labels[0])
            self._open_output_button.configure(state=tk.NORMAL)
        else:
            self._artifact_var.set("")
            self._open_output_button.configure(state=tk.DISABLED)
        if self._outputs.citation_count:
            self._citation_spin.configure(to=max(1, self._outputs.citation_count))
            self._citation_var.set("1")
            self._open_citation_button.configure(state=tk.NORMAL)
        else:
            self._open_citation_button.configure(state=tk.DISABLED)

    def _stop_run(self) -> None:
        self._runner.terminate()
        self._status_var.set("Stop requested")

    # -- open flows -----------------------------------------------------------

    def _open_output(self) -> None:
        selection = self._artifact_var.get()
        artifact = next(
            (
                artifact
                for artifact in self._outputs.artifacts
                if f"{artifact.origin}: {artifact.path}" == selection
            ),
            None,
        )
        if artifact is None:
            messagebox.showerror(
                "Open output", "No verified output is selected.", parent=self._root
            )
            return
        try:
            open_with_default_handler(artifact.path)
        except UiError as error:
            messagebox.showerror("Open output", str(error), parent=self._root)

    def _open_citation(self) -> None:
        if self._outputs.run_json is None:
            messagebox.showerror(
                "Open preserved source",
                "The last run produced no structured citations.",
                parent=self._root,
            )
            return
        try:
            number = int(self._citation_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Open preserved source",
                "Citation number must be an integer.",
                parent=self._root,
            )
            return
        try:
            path = resolve_citation_path(
                self._outputs.run_json, citation_number=number, home=self._home
            )
        except (UiError, ValueError) as error:
            messagebox.showerror("Open preserved source", str(error), parent=self._root)
            return
        try:
            open_with_default_handler(path)
        except UiError as error:
            messagebox.showerror("Open preserved source", str(error), parent=self._root)


def launch_console(*, home: Path | None = None, app: typer.Typer | None = None) -> int:
    """Open the test console window; exit cleanly when no display is present."""

    cli_app = app
    if cli_app is None:
        from archiv.cli import app as cli_app
    schema = collect_console_schema(cli_app)
    try:
        root = tk.Tk()
    except TclError:
        print(
            "archiv ui: no graphical display is available (DISPLAY is not set); "
            "run the console on a local desktop session",
            file=sys.stderr,
        )
        return 1
    ConsoleApp(root, schema=schema, home=home)
    root.mainloop()
    return 0
