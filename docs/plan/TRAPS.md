# Environment traps

Things that have already cost time. Read before debugging anything that looks broken.

## The system interpreter is too old

`python3` on a fresh container is **3.11**; this project requires **3.12+**.

```bash
uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
```

`uv` lives at `/root/.local/bin/uv` in the standard remote environment.

## `pyright` reports thousands of phantom errors

Run bare, `pyright` may not resolve the virtual environment and reports around **2,949**
errors that are all variations of "type of X is unknown" — every one an artefact of
unresolved third-party stubs, not a real defect.

```bash
pyright --pythonpath .venv/bin/python     # 0 errors, 174 files, ~10s
```

If you see a four-digit error count, this is why. Do not start "fixing" them.

## Two tests fail for environmental reasons and are not defects

On a container without a system Python 3.12 and without `archiv` on `PATH`:

| Test | Why |
|---|---|
| `tests/test_upgrade_and_installer.py::test_fedora_installer_local_source_and_upgrade` | The installer script refuses a system `python3` older than 3.12 |
| `tests/test_field_trial.py::test_public_benchmark_executes_end_to_end` | Shells out to the `archiv` binary by name |

Putting `.venv/bin` on `PATH` fixes the second — `tests/test_field_trial.py` then passes
18/18. Do not "fix" either by changing product code.

```bash
PATH="$PWD/.venv/bin:$PATH" pytest -q
```

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

Measured on `d9a9b8d` in a clean 3.12 venv:

| Check | Result |
|---|---:|
| `ruff format --check .` | 256 files already formatted |
| `ruff check .` | All checks passed |
| `pyright --pythonpath .venv/bin/python` | 0 errors, 174 files |
| `pytest -q` | 384 passed, 2 failed (both above), 2 skipped |
| line coverage | 82% (9,545 statements) |

If your numbers differ materially from these, something you did caused it.

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
