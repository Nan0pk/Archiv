# Archiv Test Console

The Archiv Test Console is the smallest human-facing wrapper over the existing command layer. It is intended for early product testing, demonstrations, and troubleshooting. It is not a separate execution engine, document viewer, Office editor, chat product, workflow builder, or generic shell.

Launch it on a desktop Fedora session with:

```bash
archiv ui
```

## Behavior

The console reads Archiv's actual Typer command tree when it starts. Public leaf commands appear in a dropdown, including nested commands such as `model configure`. Selecting a command creates controls from the real argument metadata:

- text and numeric fields;
- choice dropdowns;
- boolean checkboxes;
- file and folder path fields with native pickers;
- required-field indicators and CLI help text.

The console shows the exact equivalent `archiv ...` invocation, but it never sends that text to a shell. It executes the current Python environment with an argument vector and `shell=False`.

One operation runs at a time. The interface shows the current command, indeterminate activity, live merged standard output and error output, cancellation, and the final exit status. It does not invent percentage completion when the underlying command has no measured progress events.

## Opening results

After a successful command, valid JSON output is retained only in a private temporary directory for the lifetime of the console. Existing absolute paths found in explicit structured path fields are shown as open actions and passed to the operating system's default handler.

Citations are handled more strictly. The console reloads the current JSON result through Archiv's existing citation-file parser, revalidates the citation and immutable source through the bounded source-location capability, and only then opens the preserved source with the default handler. Text appearing in an answer is never interpreted as a path or command.

## Dependencies

The Fedora installer adds `python3-tkinter` for the native window and `xdg-utils` for default-application opening. Core CLI commands remain importable and usable in headless environments because Tk is imported only when `archiv ui` is launched.

If Tk or the operating system opener is missing, the console fails clearly without affecting the CLI.

## Deliberate limits

The first console does not provide:

- an embedded PDF, image, Office, or InPage viewer;
- editing or mutation of canonical originals;
- arbitrary shell commands;
- background jobs or autonomous agents;
- cloud synchronization or remote access;
- document thumbnails, collections, or workflow design.

Those additions require separate evidence that the simple wrapper is insufficient. The console exists to make current Archiv capabilities easy to exercise before broader product-interface decisions are made.
