# Environment traps

Things that have already cost time. Read before debugging anything that looks broken.

## The system interpreter is too old

`python3` on a fresh container is **3.11**; this project requires **3.12+**.

```bash
uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
```

`uv` lives at `$HOME/.local/bin/uv` in the standard remote environment. Written with
`$HOME` rather than the literal path on purpose: a tracked file containing a real home
directory is what `tests/test_privacy_and_artifacts.py` exists to catch, and this file
used to trip it.

## `pyright` reports thousands of phantom errors

Run bare, `pyright` may not resolve the virtual environment and reports around **2,949**
errors that are all variations of "type of X is unknown" — every one an artefact of
unresolved third-party stubs, not a real defect.

```bash
pyright --pythonpath .venv/bin/python     # 0 errors, 189 files, ~10s
```

If you see a four-digit error count, this is why. Do not start "fixing" them.

## Two tests fail for environmental reasons, and both are avoidable

On a container without a system Python 3.12 and without `archiv` on `PATH`:

| Test | Why |
|---|---|
| `tests/test_upgrade_and_installer.py::test_fedora_installer_local_source_and_upgrade` | The installer script refuses a system `python3` older than 3.12 |
| `tests/test_field_trial.py::test_public_benchmark_executes_end_to_end` | Shells out to the `archiv` binary by name |

Putting `.venv/bin` on `PATH` fixes **both** — the venv supplies a 3.12 `python3` as well
as the `archiv` binary. Do not "fix" either by changing product code.

```bash
PATH="$PWD/.venv/bin:$PATH" pytest -q
```

Run that way, **nothing fails**. Every failure is real.

This file used to be the exception: it wrote out a literal home directory, which is
exactly what `tests/test_privacy_and_artifacts.py` is for, so that test failed and the
failure was recorded here as "environmental". It was not environmental — the test was
right and the document was wrong. A permanently-failing test that everyone has agreed to
ignore is how the next real failure gets ignored too, so it is fixed rather than
explained.

Reports of "three environmental failures" come from running `pytest` without that `PATH`.
That count is also wrong, and generously so: measured here, a bare `pytest -q` does not
fail three tests, it fails to collect at all — 58 collection errors and exit 2, because
the `pytest` first on `PATH` belongs to an interpreter that has none of this project's
dependencies. A run that never executed a test is not a run with three known failures.

## Optional binaries are absent, so those paths skip

No LibreOffice, Tesseract, `bwrap`, `resvg`, `pdftoppm` or tkinter in a default container.
Consequences that look like problems but are not:

- `reports/validation.py` sits at ~50% line coverage because the LibreOffice render and
  rasterise paths cannot run. It is the most safety-critical validator in the tree; the
  gap is environmental.
- Two tests skip by design (`test_visual_ocr.py`, `test_capability_expansion_speedups.py`).
- `tests/conftest.py` sets `ARCHIV_OCR=off` for every test via an autouse fixture, so
  nothing accidentally depends on a host OCR install. Tests that need OCR install a fake
  `tesseract` shim into `tmp_path/bin` and prepend it to `PATH`.

## Known-good baseline

Measured on `1edeb92`, the head of
[#139](https://github.com/Nan0pk/Archiv/pull/139), in a clean 3.12 venv. Each check was
run on its own and its exit code read:

| Check | Result |
|---|---:|
| `ruff format --check .` | 311 files already formatted |
| `ruff check .` | All checks passed |
| `pyright --pythonpath .venv/bin/python` | 0 errors, 189 files analysed |
| `PATH="$PWD/.venv/bin:$PATH" pytest -q` | 491 passed, 0 failed, 2 skipped |
| plain `pytest -q`, no venv on `PATH` | collects nothing: 58 collection errors, exit 2 |

If your numbers differ materially from these, something you did caused it.

Two rows changed meaning rather than drifting, and both are worth knowing. There is no
longer a failing test in the first run — see above for why the one that used to be there
was not environmental. And the second run does not fail three tests; it never reaches a
test at all, because the interpreter it finds has none of the dependencies installed.
Line coverage is not listed because it was not measured here, and the figure that used to
sit in this table was carried over from a commit no longer reachable in this repository.

## Two code-path traps worth knowing before you touch the model

- **`ask` and `report` do not share the model call.** `src/archiv/tasks.py:127-152`
  re-implements it and does **not** route through `grounding.py::run_grounded_ask`.
  Anything that must appear in both has to be threaded through twice. This has already
  produced one live bug: `report_cli.py` and `mcp_tools.py` never pass `model_identity`,
  so DOCX files generated through those paths always print
  `Model identity: disabled` regardless of what actually ran.
- **`ModelConfig`'s validator and `build_model_adapter` both branch on `disabled` and
  treat everything else as loopback** (`model_adapter.py:29-51`, `:135-138`). A new
  adapter value added carelessly is silently subjected to the loopback URL rules — or
  worse, escapes them. Step S02 exists to make that dispatch explicit before S04 adds a
  third adapter.

## Locators propagate for free

`reports/formatting.py:37-38` renders **every unrecognised key** in a segment locator. So
adding a key to a locator automatically surfaces it in `archiv find` output, `archiv ask`
output, the DOCX source-overview table and the source appendix, with no further edits.
This is the cheapest way to attribute derived content — and the easiest way to leak
something you did not mean to show. Both directions are deliberate; check which one you
are in.

## `tiktoken.get_encoding` downloads at runtime, which this project forbids

Counting tokens for a paid request needs the provider's own tokenizer. The obvious call
is `tiktoken.get_encoding("o200k_base")`, and it **fetches the encoding over the network**
the first time it is asked for one — from `openaipublic.blob.core.windows.net`.

That breaks the hard rule that no processor may download anything at runtime, and it also
simply fails in the standard remote container, where the proxy refuses the host:

```
ProxyError: Unable to connect to proxy ... Tunnel connection failed: 403 Forbidden
```

So `src/archiv/cost_control.py` never calls `get_encoding`. It loads an encoding only
from a file already on disk whose SHA-256 is recorded in `config/spend.json`, using
`tiktoken.load.load_tiktoken_bpe` with `expected_hash`, and treats every failure as "no
tokenizer" rather than as a reason to fetch one. That is the same shape as installed OCR
languages: a pinned artefact or a recorded skip.

The consequence, which is deliberate: with no pinned encoding, a prompt cannot be counted,
so no cost is projected before a call. The ceiling still holds — it is enforced against
recorded spend and a hard cap on reply length — but a single oversized call cannot be
refused for its size before it is made. `archiv model spend status` says which of those
two situations an archive is in.

### What the spend ceiling does and does not stop

With no pinned encoding there is no prompt count, so there is no projection, so a call is
refused only once `spent_usd` has already reached `ceiling_usd`. The call that crosses the
line completes and is charged for. It is bounded — the prompt plus at most
`max_output_tokens` — but somebody reading "ceiling: $1.00" may expect a hard stop at a
dollar rather than a stop just past it. Pin an encoding and the projection refuses an
oversized call before it is made.
