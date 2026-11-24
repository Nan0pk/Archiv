"""Keyboard-accessible Tk desktop shell over Archiv's bounded commands."""

from __future__ import annotations

import json
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import TclError, filedialog, messagebox, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from archiv.doctor import diagnostics_report, save_diagnostics
from archiv.storage.layout import MissingLibraryError, remember_home
from archiv.ui.product import (
    DesktopState,
    check_prerequisites,
    configure_model,
    detect_loopback_models,
    gguf_hint,
    ingestion_argv,
    list_documents,
    load_state,
    question_argv,
    save_state,
    search_argv,
    verify_model,
)
from archiv.ui.runner import ConsoleRunner, console_executable_argv

_POLL_MS = 100


# The CLI prints this above any answer that did not come from this machine.
_NOT_LOCAL_MARKER = "NOT A LOCAL ANSWER"


def finished_status(label: str, output: str, *, succeeded: bool) -> str:
    """The status line after a run. The not-local warning is repeated only from where
    `ask` prints its banner: above the "Question:" line of a question run. Anywhere else
    the words could be a quoted passage from a document."""
    state = "Finished" if succeeded else "Needs attention — review details"
    if label == "Questions" and _NOT_LOCAL_MARKER in output.split("Question:", 1)[0]:
        state = f"{state} — NOT A LOCAL ANSWER: this archive is in evaluation mode"
    return state


class ProductApp:
    """Persistent task-oriented application; no command construction is exposed."""

    VIEWS = (
        "Library",
        "Needs attention",
        "Search",
        "Questions",
        "Reports",
        "Settings",
    )

    def __init__(self, root: tk.Tk, *, home: Path | None = None) -> None:
        self.root = root
        self.state = load_state(home)
        self._pending: list[str] = []
        self._running = ""
        self.runner = ConsoleRunner(on_output=self._on_output)
        root.title("Archiv — your document library")
        if "clam" in ttk.Style(root).theme_names():
            ttk.Style(root).theme_use("clam")
        root.minsize(900, 620)
        root.bind("<Control-k>", lambda _event: self._show("Search"))
        root.bind("<Control-q>", lambda _event: self._show("Questions"))
        root.protocol("WM_DELETE_WINDOW", self._close)

        # Packed before the sidebar and body so it spans the full width at the bottom.
        self.status = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W, padding=4).pack(
            side=tk.BOTTOM, fill=tk.X
        )
        base = tkfont.nametofont("TkDefaultFont")
        self.title_font = base.copy()
        self.title_font.configure(size=max(base.cget("size"), 10) + 8, weight="bold")
        self.section_font = base.copy()
        self.section_font.configure(weight="bold")
        sidebar = ttk.Frame(root, padding=12)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(sidebar, text="Archiv", font=self.title_font).pack(anchor=tk.W)
        # The folder name only: a full path wraps over several lines and pushes the
        # navigation off the window. Settings shows the whole path.
        ttk.Label(sidebar, text=self.state.home.name or str(self.state.home)).pack(
            anchor=tk.W, pady=(0, 14)
        )
        for view in self.VIEWS:
            ttk.Button(sidebar, text=view, command=lambda name=view: self._show(name)).pack(
                fill=tk.X, pady=2
            )
        self.body = ttk.Frame(root, padding=18)
        self.body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if self.state.onboarding_complete:
            self._show("Library")
        else:
            self._onboard()

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _heading(self, title: str, detail: str) -> None:
        ttk.Label(self.body, text=title, font=self.title_font).pack(anchor=tk.W)
        ttk.Label(self.body, text=detail, wraplength=650).pack(anchor=tk.W, pady=(2, 16))

    def _onboard(self) -> None:
        self._clear()
        self._heading(
            "Welcome to Archiv",
            "Choose where Archiv keeps its library, your first document folder, and the AI "
            "model on this computer that answers questions. Nothing leaves this computer.",
        )
        home = tk.StringVar(value=str(self.state.home))
        folder = tk.StringVar()
        for label, variable, chooser in (
            (
                "Library folder (where Archiv keeps its copies and index)",
                home,
                lambda: filedialog.askdirectory(title="Choose library folder"),
            ),
            ("First document folder", folder, lambda: filedialog.askdirectory(title="Add folder")),
        ):
            ttk.Label(self.body, text=label).pack(anchor=tk.W)
            row = ttk.Frame(self.body)
            row.pack(fill=tk.X, pady=(2, 10))
            ttk.Entry(row, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(
                row, text="Browse…", command=lambda v=variable, c=chooser: v.set(c() or v.get())
            ).pack(side=tk.LEFT, padx=4)
        checks = check_prerequisites()
        ttk.Label(self.body, text="Optional document support", font=self.section_font).pack(
            anchor=tk.W
        )
        for check in checks:
            mark = "Available" if check.available else "Action needed"
            ttk.Label(self.body, text=f"{check.name}: {mark}. {check.guidance}").pack(anchor=tk.W)
        chosen_model = self._model_picker()

        def finish() -> None:
            selected_home, selected_folder = (
                Path(home.get()).expanduser(),
                Path(folder.get()).expanduser(),
            )
            if not selected_folder.is_dir():
                messagebox.showerror(
                    "Folder required", "Choose an existing document folder.", parent=self.root
                )
                return
            model = chosen_model()
            if model is not None:
                try:
                    configure_model(selected_home, *model)
                except ValueError as error:
                    messagebox.showerror("Model not saved", str(error), parent=self.root)
                    return
                self.status.set(f"Checking that {model[1]} answers…")
                self.root.update_idletasks()
                ok, message = verify_model(selected_home)
                if not ok:
                    messagebox.showwarning(
                        "Model saved, but not answering",
                        f"{message}\n\nStart the model server, then choose it again in Settings.",
                        parent=self.root,
                    )
            self.state = DesktopState(selected_home.resolve(), (selected_folder.resolve(),), ())
            save_state(self.state)
            remember_home(self.state.home)  # so the next launch opens this library
            self._start(
                ingestion_argv(selected_folder, self.state.home), "Adding your first folder"
            )
            self._show("Library")

        ttk.Button(self.body, text="Create library and add folder", command=finish).pack(
            anchor=tk.W, pady=18
        )

    def _model_picker(self) -> Callable[[], tuple[str, str] | None]:
        """Pack a model chooser into the body; the returned function reads the choice."""

        ttk.Label(self.body, text="AI model", font=self.section_font).pack(anchor=tk.W, pady=(8, 0))
        frame = ttk.Frame(self.body)
        frame.pack(fill=tk.X, anchor=tk.W)
        skip = "None for now (search still works)"
        manual = "Enter a server address by hand"
        choice = tk.StringVar()
        endpoint = tk.StringVar(value="http://127.0.0.1:11434")
        name = tk.StringVar()
        found: list[tuple[str, str, str]] = []
        combo = ttk.Combobox(frame, textvariable=choice, state="readonly", width=60)
        combo.pack(side=tk.LEFT)
        manual_row = ttk.Frame(self.body)
        for label, variable in (("Server address", endpoint), ("Model name", name)):
            ttk.Label(manual_row, text=label).pack(side=tk.LEFT)
            ttk.Entry(manual_row, textvariable=variable, width=28).pack(side=tk.LEFT, padx=4)

        def labels() -> list[str]:
            return [f"{model} ({server})" for _endpoint, server, model in found]

        def refresh() -> None:
            found[:] = detect_loopback_models()
            combo["values"] = [*labels(), manual, skip]
            choice.set(labels()[0] if found else skip)
            on_choice()

        def on_choice(_event: object = None) -> None:
            if choice.get() == manual:
                manual_row.pack(anchor=tk.W, pady=4, after=frame)
            else:
                manual_row.pack_forget()

        def model_file() -> None:
            selected = filedialog.askopenfilename(
                title="Choose a model file", filetypes=[("GGUF model", "*.gguf"), ("All", "*")]
            )
            if selected:
                messagebox.showinfo(
                    "Serving a model file", gguf_hint(Path(selected)), parent=self.root
                )

        combo.bind("<<ComboboxSelected>>", on_choice)
        ttk.Button(frame, text="Look again", command=refresh).pack(side=tk.LEFT, padx=4)
        ttk.Button(frame, text="I have a model file…", command=model_file).pack(side=tk.LEFT)
        refresh()

        def read() -> tuple[str, str] | None:
            if choice.get() == manual:
                return endpoint.get(), name.get()
            if choice.get() in labels():
                hit = found[labels().index(choice.get())]
                return hit[0], hit[2]
            return None

        return read

    def _show(self, view: str) -> None:
        self._clear()
        if view in {"Library", "Needs attention"}:
            failed = view == "Needs attention"
            self._heading(
                view,
                "Files that could not be indexed include a recovery action."
                if failed
                else "Documents preserved and indexed in this library.",
            )
            rows = list_documents(self.state.home, failures=failed)
            if not rows:
                text = (
                    "Nothing needs attention."
                    if failed
                    else "No indexed documents yet. Add a folder in Settings to begin."
                )
                ttk.Label(self.body, text=text).pack(anchor=tk.W)
                return
            ttk.Label(self.body, text=f"{len(rows):,} file(s)").pack(anchor=tk.W, pady=(0, 6))
            last = "Problem" if failed else "Added"
            table = ttk.Treeview(
                self.body, columns=("name", "folder", last), show="headings", selectmode="browse"
            )
            char = tkfont.nametofont("TkDefaultFont").measure("0")  # widths follow the font size
            for column, chars in (("name", 26), ("folder", 30), (last, 18)):
                table.heading(column, text=column.capitalize(), anchor=tk.W)
                table.column(
                    column, width=chars * char, minwidth=8 * char, stretch=column == "folder"
                )
            for row in rows:
                path = Path(row.source_path)
                detail = (row.error or "") if failed else row.imported_at[:16].replace("T", " ")
                table.insert("", tk.END, values=(row.name, str(path.parent), detail))
            scroll = ttk.Scrollbar(self.body, orient=tk.VERTICAL, command=table.yview)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            table.configure(yscrollcommand=scroll.set)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            table.pack(fill=tk.BOTH, expand=True)
            if failed:
                ttk.Label(
                    self.body,
                    text=(
                        "Check file permissions or install the prerequisite shown in "
                        "Settings, then add the containing folder again."
                    ),
                ).pack(anchor=tk.W, pady=12)
        elif view in {"Search", "Questions"}:
            self._heading(
                view,
                "Search verified text (Ctrl+K)."
                if view == "Search"
                else "Ask a grounded question (Ctrl+Q).",
            )
            value = tk.StringVar()
            entry = ttk.Entry(self.body, textvariable=value)
            entry.pack(fill=tk.X)
            output = ScrolledText(self.body, height=20, wrap=tk.WORD, state=tk.DISABLED)
            output.pack(fill=tk.BOTH, expand=True, pady=10)
            self.output = output
            action = search_argv if view == "Search" else question_argv
            if view == "Questions" and self.state.recent_questions:
                ttk.Label(
                    self.body,
                    text="Recent: " + " · ".join(self.state.recent_questions[:5]),
                    wraplength=700,
                ).pack(anchor=tk.W)

            def submit() -> None:
                try:
                    argv = action(value.get(), self.state.home)
                except ValueError as error:
                    self.status.set(str(error))
                    entry.focus_set()
                    return
                if view == "Questions":
                    self.state = DesktopState(
                        self.state.home,
                        self.state.folders,
                        (value.get().strip(), *self.state.recent_questions)[:20],
                    )
                    save_state(self.state)
                self._start(argv, view)

            ttk.Button(
                self.body,
                text="Search" if view == "Search" else "Ask",
                command=submit,
            ).pack(side=tk.LEFT)
            ttk.Button(self.body, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=5)
            entry.bind("<Return>", lambda _event: submit())
            entry.focus_set()
        elif view == "Reports":
            self._heading(
                "Reports", "Validated reports created by Archiv appear in the outputs folder."
            )
            reports = sorted(self.state.home.joinpath("outputs").glob("**/*.docx"), reverse=True)
            ttk.Label(
                self.body,
                text="No reports yet." if not reports else "\n".join(path.name for path in reports),
            ).pack(anchor=tk.W)
        else:
            self._heading(
                "Settings", "Library location, watched folders, and optional capabilities."
            )
            ttk.Label(self.body, text=f"Archiv home: {self.state.home}").pack(anchor=tk.W)
            for folder in self.state.folders:
                ttk.Label(self.body, text=f"Folder: {folder}").pack(anchor=tk.W)
            for check in check_prerequisites():
                ttk.Label(
                    self.body,
                    text=f"{check.name}: {'available' if check.available else check.guidance}",
                ).pack(anchor=tk.W, pady=2)
            ttk.Button(self.body, text="Add folder…", command=self._add_folder).pack(
                anchor=tk.W, pady=12
            )
            chosen_model = self._model_picker()

            def save_model() -> None:
                model = chosen_model()
                if model is None:
                    self.status.set("No model chosen.")
                    return
                try:
                    configure_model(self.state.home, *model)
                except ValueError as error:
                    messagebox.showerror("Model not saved", str(error), parent=self.root)
                    return
                self.status.set(f"Checking that {model[1]} answers…")
                self.root.update_idletasks()
                ok, message = verify_model(self.state.home)
                self.status.set(
                    ("Model ready: " if ok else "Saved, but the check failed: ") + message
                )

            ttk.Button(self.body, text="Use this model", command=save_model).pack(
                anchor=tk.W, pady=4
            )
            ttk.Button(self.body, text="Export diagnostics…", command=self._diagnostics).pack(
                anchor=tk.W, pady=4
            )

    def _diagnostics(self) -> None:
        report = diagnostics_report(self.state.home)
        preview = tk.Toplevel(self.root)
        preview.title("Preview diagnostics — every field")
        ttk.Label(preview, text="Review every field below before choosing Save.").pack(
            padx=12, pady=8
        )
        text = ScrolledText(preview, width=90, height=30, wrap=tk.NONE)
        text.insert("1.0", json.dumps(report, indent=2, sort_keys=True))
        text.configure(state=tk.DISABLED)
        text.pack(fill=tk.BOTH, expand=True, padx=12)

        def save() -> None:
            selected = filedialog.asksaveasfilename(parent=preview, defaultextension=".json")
            if not selected:
                return
            try:
                save_diagnostics(report, Path(selected))
            except OSError:
                messagebox.showerror("Not saved", "Choose a new writable filename.", parent=preview)
                return
            messagebox.showinfo("Saved", "Diagnostics bundle saved.", parent=preview)
            preview.destroy()

        ttk.Button(preview, text="Save…", command=save).pack(side=tk.LEFT, padx=12, pady=10)
        ttk.Button(preview, text="Cancel", command=preview.destroy).pack(side=tk.LEFT, pady=10)

    def _add_folder(self) -> None:
        selected = filedialog.askdirectory(title="Add document folder")
        if not selected:
            return
        folder = Path(selected).resolve()
        self.state = DesktopState(
            self.state.home, (*self.state.folders, folder), self.state.recent_questions
        )
        save_state(self.state)
        self._start(ingestion_argv(folder, self.state.home), "Adding folder")

    def _start(self, argv: list[str], label: str) -> None:
        if self.runner.busy:
            messagebox.showinfo(
                "Archiv is busy", "Cancel or wait for the current operation.", parent=self.root
            )
            return
        # Cleared before each run. Without this the output box accumulates, and the
        # "not a local answer" notice prepended below would stack -- three identical
        # notices at the top with the newest answer off the bottom of the screen, which
        # makes a warning ambiguous about which answer it belongs to.
        self._running = label
        self._pending.clear()
        self._clear_output()
        self.runner.start(console_executable_argv(argv))
        self.status.set(label + "…")
        self.root.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        self._drain_output()
        if self.runner.busy:
            self.root.after(_POLL_MS, self._poll)
            return
        self._drain_output()
        outcome = self.runner.wait(timeout=0)
        state = finished_status(self._running, outcome.output, succeeded=outcome.succeeded)
        self.status.set(state)

    def _on_output(self, chunk: bytes) -> None:
        # Called on the runner's thread. Tk must only be touched from its own thread, so
        # queue the text here; `_poll` writes it into the window (an earlier version
        # called `root.after` from this thread and the text never appeared).
        self._pending.append(chunk.decode("utf-8", errors="replace"))

    def _drain_output(self) -> None:
        while self._pending:
            self._append(self._pending.pop(0))

    def _live_output(self) -> ScrolledText | None:
        """The output box, if one exists right now -- otherwise nothing.

        `self.output` outlives the widget it points at. Switching view calls `_clear`,
        which destroys every child of the body frame, while the attribute keeps pointing
        at the destroyed widget. So `hasattr` is not a sufficient guard: touching a
        destroyed widget raises `TclError` from inside a Tk callback, which loses the
        whole action -- ask a question, switch to Settings, click "Add folder", and the
        folder is never added and the window says nothing about why.
        """

        widget: ScrolledText | None = getattr(self, "output", None)
        if widget is None:
            return None
        try:
            return widget if widget.winfo_exists() else None
        except TclError:
            return None

    def _write_output(self, text: str | None, *, at: str) -> None:
        """Write into the output box, or clear it when `text` is None. Never raises."""

        widget = self._live_output()
        if widget is None:
            return
        try:
            widget.configure(state=tk.NORMAL)
            if text is None:
                widget.delete("1.0", tk.END)
            else:
                widget.insert(at, text)
            widget.configure(state=tk.DISABLED)
        except TclError:
            # Destroyed between the check and the write. Losing the text is correct;
            # taking down the callback that was going to do the real work is not.
            return

    def _clear_output(self) -> None:
        self._write_output(None, at="1.0")

    def _append(self, text: str) -> None:
        self._write_output(text, at=tk.END)

    def _cancel(self) -> None:
        if self.runner.busy and messagebox.askyesno(
            "Cancel operation?", "Stop the current operation?", parent=self.root
        ):
            self.runner.terminate()
            self.status.set("Cancellation requested")

    def _close(self) -> None:
        if self.runner.busy and not messagebox.askyesno(
            "Quit Archiv?", "An operation is running. Stop it and quit?", parent=self.root
        ):
            return
        if self.runner.busy:
            self.runner.terminate()
        self.root.destroy()


def launch_product(*, home: Path | None = None) -> int:
    try:
        root = tk.Tk()
    except TclError:
        print(
            "archiv ui: no graphical display is available; use a local desktop session",
            file=sys.stderr,
        )
        return 1
    try:
        ProductApp(root, home=home)
    except MissingLibraryError as error:
        messagebox.showerror("Archiv library not found", str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0
