"""Keyboard-accessible Tk desktop shell over Archiv's bounded commands."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import TclError, filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from archiv.ui.product import (
    DesktopState,
    check_prerequisites,
    configure_detected_model,
    detect_loopback_model,
    ingestion_argv,
    list_documents,
    load_state,
    question_argv,
    save_state,
    search_argv,
)
from archiv.ui.runner import ConsoleRunner, console_executable_argv

_POLL_MS = 100


class ProductApp:
    """Persistent task-oriented application; no command construction is exposed."""

    VIEWS = (
        "Library",
        "Needs attention",
        "Search",
        "Questions",
        "Sources",
        "Reports",
        "Settings",
    )

    def __init__(self, root: tk.Tk, *, home: Path | None = None) -> None:
        self.root = root
        self.state = load_state(home)
        self.runner = ConsoleRunner(on_output=self._on_output)
        root.title("Archiv — your document library")
        root.minsize(900, 620)
        root.bind("<Control-k>", lambda _event: self._show("Search"))
        root.bind("<Control-q>", lambda _event: self._show("Questions"))
        root.protocol("WM_DELETE_WINDOW", self._close)

        sidebar = ttk.Frame(root, padding=12)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(sidebar, text="Archiv", font=("TkDefaultFont", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(sidebar, text=str(self.state.home), wraplength=190).pack(
            anchor=tk.W, pady=(0, 14)
        )
        for view in self.VIEWS:
            ttk.Button(sidebar, text=view, command=lambda name=view: self._show(name)).pack(
                fill=tk.X, pady=2
            )
        self.body = ttk.Frame(root, padding=18)
        self.body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(root, textvariable=self.status, relief=tk.SUNKEN, anchor=tk.W).pack(
            side=tk.BOTTOM, fill=tk.X
        )
        if self.state.onboarding_complete:
            self._show("Library")
        else:
            self._onboard()

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _heading(self, title: str, detail: str) -> None:
        ttk.Label(self.body, text=title, font=("TkDefaultFont", 20, "bold")).pack(anchor=tk.W)
        ttk.Label(self.body, text=detail, wraplength=650).pack(anchor=tk.W, pady=(2, 16))

    def _onboard(self) -> None:
        self._clear()
        self._heading("Welcome to Archiv", "Choose where Archiv lives, then add your first folder.")
        home = tk.StringVar(value=str(self.state.home))
        folder = tk.StringVar()
        for label, variable, chooser in (
            ("Archiv home", home, lambda: filedialog.askdirectory(title="Choose Archiv home")),
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
        ttk.Label(
            self.body, text="Optional document support", font=("TkDefaultFont", 11, "bold")
        ).pack(anchor=tk.W)
        for check in checks:
            mark = "Available" if check.available else "Action needed"
            ttk.Label(self.body, text=f"{check.name}: {mark}. {check.guidance}").pack(anchor=tk.W)
        detected = detect_loopback_model()
        use_model = tk.BooleanVar(value=detected is not None)
        if detected is not None:
            ttk.Checkbutton(
                self.body,
                text=f"Use detected loopback model ({detected[1]})",
                variable=use_model,
            ).pack(anchor=tk.W, pady=8)

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
            self.state = DesktopState(selected_home.resolve(), (selected_folder.resolve(),), ())
            save_state(self.state)
            if detected is not None and use_model.get():
                configure_detected_model(self.state.home, detected)
            self._start(
                ingestion_argv(selected_folder, self.state.home), "Adding your first folder"
            )
            self._show("Library")

        ttk.Button(self.body, text="Create library and add folder", command=finish).pack(
            anchor=tk.W, pady=18
        )

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
                    else "No indexed documents yet. Add a folder to begin."
                )
                ttk.Label(self.body, text=text).pack(anchor=tk.W)
            for row in rows:
                detail = row.error or row.source_path
                ttk.Label(self.body, text=f"{row.name} — {detail}", wraplength=700).pack(
                    anchor=tk.W, pady=3
                )
            if failed:
                ttk.Label(
                    self.body,
                    text=(
                        "Check file permissions or install the prerequisite shown in "
                        "Settings, then retry the containing folder."
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
                argv = action(value.get(), self.state.home)
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
                text=view.removesuffix("s"),
                command=submit,
            ).pack(side=tk.LEFT)
            ttk.Button(self.body, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=5)
            entry.bind("<Return>", lambda _event: submit())
            entry.focus_set()
        elif view == "Sources":
            self._heading(
                "Sources",
                (
                    "Verified citations from search, questions, and reports identify "
                    "preserved sources."
                ),
            )
            ttk.Label(
                self.body,
                text=(
                    "Open a citation from its result. Archiv revalidates its bounded source "
                    "location before handing it to the desktop."
                ),
                wraplength=700,
            ).pack(anchor=tk.W)
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
        self.runner.start(console_executable_argv(argv))
        self.status.set(label + "…")
        self.root.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        if self.runner.busy:
            self.root.after(_POLL_MS, self._poll)
            return
        outcome = self.runner.wait(timeout=0)
        self.status.set("Finished" if outcome.succeeded else "Needs attention — review details")

    def _on_output(self, chunk: bytes) -> None:
        if not hasattr(self, "output"):
            return
        text = chunk.decode("utf-8", errors="replace")
        self.root.after(0, lambda: self._append(text))

    def _append(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.configure(state=tk.DISABLED)

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
    ProductApp(root, home=home)
    root.mainloop()
    return 0
