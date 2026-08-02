# Bounded source location

`archiv source` moves from one explicit Archiv reference to its immutable preserved original without becoming a general file browser or launcher.

## Command forms

Resolve a canonical object digest:

```bash
archiv source <lowercase-object-sha256>
```

Resolve a citation selected from common Archiv JSON output:

```bash
archiv find "quarterly finding" --json > matches.json
archiv source --citation-file matches.json --citation-number 1
```

The citation file may contain:

- one raw `Citation` JSON object;
- one search result with a nested `citation`;
- a list produced by `archiv find --json`;
- an ask result with `retrieved_citations`;
- a report manifest with numbered `sources` containing citations.

Selection is one-based and explicit. An out-of-range number fails rather than guessing.

## Validation boundary

Before returning a location, Archiv:

1. requires a lowercase 64-character SHA-256 object identifier;
2. resolves the expected content-addressed original under the active Archiv home;
3. rejects symbolic links in the source path chain;
4. rejects any resolved path outside Archiv-controlled original storage;
5. recomputes the original SHA-256 and requires an exact match;
6. loads the canonical normalized document from bounded derived storage;
7. checks normalized object, source, media type, and kind metadata;
8. for citation input, runs the full existing citation validator, including normalized hash, segment index, native locator, segment ID, and text hash checks.

Failure is non-zero and does not return a guessed path.

## Output

Human output shows the source name, media type, normalized kind, object SHA-256, native locator when a citation was supplied, preserved local path, validation status, and read-only mode.

`--json` returns the versioned `SourceLocation` contract with:

- `reference_type` (`object` or `citation`);
- object SHA-256;
- source name, media type, and kind;
- native locator or `null` for whole-object lookup;
- canonical absolute and Archiv-relative paths;
- citation and original-hash validation flags;
- `read_only: true`.

Absolute paths are local operator output. Sanitized public field-trial and host-acceptance summaries retain only pass/fail validation booleans and do not publish the returned path.

## Explicit non-goals

The command does not:

- execute, render, edit, or parse the original again;
- invoke a shell, desktop opener, URL handler, macro, formula, script, or embedded binary;
- follow a source symlink;
- browse arbitrary filesystem paths;
- upload or generate cloud links;
- change citation identity or the Archiv storage layout.
