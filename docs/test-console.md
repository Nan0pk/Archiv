# Archiv desktop and diagnostic console

`archiv ui` opens the user-oriented desktop application. It is organized around
the library, ingestion progress, search, grounded questions, sources, reports,
recovery, and settings. First run chooses an Archiv home and initial folder and
checks optional OCR and LibreOffice support. Long operations can be cancelled,
and keyboard shortcuts include Ctrl+K for search and Ctrl+Q for questions.

The original command-oriented test console is retained for maintainers as
`archiv ui --diagnostic`. It remains a **diagnostic console over the existing
command layer**, not the product interface.

## Behavior

- launches locally with `archiv ui --diagnostic`;
- derives the available commands and arguments from Archiv's real Typer
  command tree at startup, so the console can never drift from the CLI;
- presents a command dropdown and generates only the argument controls that
  command needs (nested commands such as `model configure` are included);
- provides path-entry boxes with native file and folder pickers for path
  parameters, checkboxes for booleans, dropdowns for choices, and validated
  fields for numbers;
- shows the exact equivalent CLI invocation as values change;
- runs one command at a time through an argument vector with `shell=False`;
  values containing spaces, quotes, or shell metacharacters remain literal
  data; command construction cannot inject a shell command;
- shows the current action, an indeterminate progress indicator while the
  command runs, live merged output, and the final exit status;
- retains the complete output of the current run (up to a deliberate
  retained-output bound that is reported honestly when reached);
- detects verified output paths after a run — files inside Archiv-controlled
  storage or explicit output paths entered in the form — and opens them
  through the operating system's default handler (`xdg-open`);
- resolves citations through Archiv's existing bounded source-location
  validator (`archiv source` semantics) before opening the preserved source:
  run a command that emits JSON citations (for example `find` with `--json`),
  pick the citation number, and the console revalidates the immutable
  original and citation, then opens the preserved file read-only.

## Scope boundary

The console does not add an embedded document viewer, a document editor, a
chat product, a workflow builder, a background agent, a cloud service, a
generic shell box, or arbitrary command execution. CLI behavior and
contracts are unchanged; the console is replaceable and removable without
affecting any verified workflow.

## Requirements and failure behavior

The console requires the `python3-tkinter` desktop package (installed by the
Fedora installer) and a graphical session (`DISPLAY`). Without them,
`archiv ui` exits non-zero with an explicit message naming the missing
dependency instead of falling back to anything. Opening files requires
`xdg-open` (from `xdg-utils`, also installed by the installer); without a
default file handler the console reports the verified path instead of
failing silently.

## Non-goals

Seasoned CLI users lose nothing by ignoring the console entirely. All
evidence formats, run ledgers, and validators remain CLI- and
contract-defined; the console merely captures the same terminal behavior
behind a form.
