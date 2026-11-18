"""Tools run on untrusted files cannot see the user's session, files or environment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from archiv.ingestion.visual_ocr import _run  # pyright: ignore[reportPrivateUsage]


def _bubblewrap_works() -> bool:
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        return False
    probe = subprocess.run(
        [bubblewrap, "--unshare-net", "--ro-bind", "/", "/", "--dev", "/dev", "--", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


# The test interpreter may itself live under a hidden folder such as /home.
SYSTEM_PYTHON = Path("/usr/bin/python3")


@pytest.mark.skipif(
    not _bubblewrap_works() or not SYSTEM_PYTHON.is_file(),
    reason="a working bubblewrap or /usr/bin/python3 is not available",
)
def test_ocr_runner_hides_session_files_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARCHIV_OCR_SANDBOX", "required")
    monkeypatch.setenv("ARCHIV_TEST_SECRET", "must-not-leak")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    probe = (
        "import json, os\n"
        f"print(json.dumps({{'outside': os.path.exists({str(outside)!r}),"
        f" 'run_user': os.path.exists('/run/user/{os.getuid()}'),"
        " 'secret': 'ARCHIV_TEST_SECRET' in os.environ,"
        " 'root_writable': os.access(os.getcwd(), os.W_OK)}))\n"
    )

    stdout, _, sandbox = _run([str(SYSTEM_PYTHON), "-c", probe], root=root, timeout=30)

    seen = json.loads(stdout)
    assert sandbox == "bubblewrap"
    assert seen == {"outside": False, "run_user": False, "secret": False, "root_writable": True}


def test_svg_rendering_goes_through_the_sandboxed_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from archiv.ingestion import derive, ingest_file, visual_ocr
    from tests.format_matrix_support import build_svg

    real_which = shutil.which

    def which(name: str) -> str | None:
        return "/usr/bin/resvg" if name == "resvg" else real_which(name)

    monkeypatch.setattr(shutil, "which", which)
    calls: list[tuple[list[str], Path, tuple[Path, ...]]] = []

    def fake_run(
        command: list[str], *, root: Path, timeout: int, extra_ro_binds: tuple[Path, ...] = ()
    ) -> tuple[str, str, str]:
        if command[0] == "/usr/bin/resvg":
            calls.append((command, root, extra_ro_binds))
            Image.new("RGB", (8, 8), "white").save(command[-1], format="PNG")
            return "", "", "bubblewrap"
        return _run(command, root=root, timeout=timeout, extra_ro_binds=extra_ro_binds)

    monkeypatch.setattr(visual_ocr, "_run", fake_run)
    monkeypatch.setattr(derive, "run_sandboxed", fake_run)
    source = tmp_path / "drawing.svg"
    source.write_bytes(build_svg())

    ingest_file(source, home=tmp_path / "home")

    assert calls, "resvg was run outside the sandboxed runner"
    for command, root, binds in calls:
        assert Path(command[-1]).is_relative_to(root)
        assert any(Path(command[-2]).parent == b for b in binds)
