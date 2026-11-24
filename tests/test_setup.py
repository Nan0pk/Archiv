"""Guided first run: the remembered library, model discovery, and `archiv setup`."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from archiv import setup_cli
from archiv.cli import app
from archiv.model_adapter import load_model_config
from archiv.storage.layout import ArchivLayout, home_pointer_path, remember_home
from archiv.ui import product

if TYPE_CHECKING:
    from tkinter import Misc, ttk


def test_remembered_library_is_used_until_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    chosen = tmp_path / "library"
    chosen.mkdir()
    remember_home(chosen)
    assert ArchivLayout.resolve().root == chosen.resolve()
    assert ArchivLayout.resolve(tmp_path / "explicit").root == (tmp_path / "explicit").resolve()
    monkeypatch.setenv("ARCHIV_HOME", str(tmp_path / "env"))
    assert ArchivLayout.resolve().root == (tmp_path / "env").resolve()


def test_missing_remembered_library_is_an_error_not_a_new_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    gone = tmp_path / "unplugged-drive"
    gone.mkdir()
    remember_home(gone)
    gone.rmdir()
    with pytest.raises(RuntimeError, match="missing"):
        ArchivLayout.resolve()
    assert not gone.exists()


class _Reply(io.BytesIO):
    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_detection_lists_every_model_and_only_asks_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> _Reply:
        asked.append(url)
        if url.startswith("http://127.0.0.1:11434"):
            return _Reply(json.dumps({"data": [{"id": "llama3"}, {"id": "qwen3"}]}).encode())
        raise OSError("connection refused")

    monkeypatch.setattr(product, "urlopen", fake_urlopen)
    found = product.detect_loopback_models()
    assert [model for *_rest, model in found] == ["llama3", "qwen3"]
    assert asked and all(url.startswith("http://127.0.0.1:") for url in asked)


def test_no_server_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: float) -> _Reply:
        raise OSError("connection refused")

    monkeypatch.setattr(product, "urlopen", refuse)
    assert product.detect_loopback_models() == ()


def test_non_loopback_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="loopback"):
        product.configure_model(tmp_path, "http://192.168.1.5:11434", "llama3")
    assert load_model_config(tmp_path).adapter == "disabled"


def test_setup_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    library, documents = tmp_path / "library", tmp_path / "documents"
    documents.mkdir()
    (documents / "note.txt").write_text("the quarterly budget was approved\n", encoding="utf-8")
    monkeypatch.setattr(
        setup_cli,
        "detect_loopback_models",
        lambda: (("http://127.0.0.1:11434", "Ollama", "llama3"),),
    )

    def answered(home: Path) -> tuple[bool, str]:
        return True, "llama3 answered."

    monkeypatch.setattr(setup_cli, "verify_model", answered)

    result = CliRunner().invoke(app, ["setup"], input=f"{library}\n{documents}\n1\n")

    assert result.exit_code == 0, result.output
    assert "Model ready" in result.output
    assert home_pointer_path().read_text(encoding="utf-8").strip() == str(library.resolve())
    config = load_model_config(library)
    assert (config.endpoint, config.model) == ("http://127.0.0.1:11434", "llama3")
    # A later command with no --home finds the remembered library and its document.
    found = CliRunner().invoke(app, ["find", "quarterly budget"])
    assert found.exit_code == 0, found.output
    assert "note.txt" in found.output


def test_setup_skips_model_and_rejects_bad_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_cli, "detect_loopback_models", lambda: ())
    result = CliRunner().invoke(
        app,
        ["setup", "--home", str(tmp_path / "lib")],
        input="\nm\nhttp://example.com:11434\nllama3\nx\ns\n",
    )
    assert result.exit_code == 0, result.output
    assert "No model server found" in result.output
    assert "Not saved" in result.output
    assert "'x' is not one of the choices" in result.output
    assert "No model chosen" in result.output
    assert load_model_config(tmp_path / "lib").adapter == "disabled"


def test_missing_library_is_reported_in_one_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archiv.cli import main

    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    remember_home(tmp_path / "gone")
    monkeypatch.setattr("sys.argv", ["archiv", "status"])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "is missing" in error
    assert "Traceback" not in error


def test_setup_with_a_missing_library_offers_no_silent_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    monkeypatch.setattr(setup_cli, "detect_loopback_models", lambda: ())
    remember_home(tmp_path / "unplugged")
    new = tmp_path / "new-library"
    # An empty first answer must be asked again, not accepted as some default folder.
    result = CliRunner().invoke(app, ["setup"], input=f"\n{new}\n\ns\n")
    assert result.exit_code == 0, result.output
    assert f"Your library at {tmp_path / 'unplugged'} is missing" in result.output
    assert home_pointer_path().read_text(encoding="utf-8").strip() == str(new.resolve())


def test_setup_warns_when_archiv_home_overrides_the_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARCHIV_HOME", str(tmp_path / "from-env"))
    monkeypatch.setattr(setup_cli, "detect_loopback_models", lambda: ())
    result = CliRunner().invoke(app, ["setup", "--home", str(tmp_path / "chosen")], input="\ns\n")
    assert result.exit_code == 0, result.output
    assert "ARCHIV_HOME is set" in result.output


def test_doctor_reports_a_missing_remembered_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archiv.doctor import doctor_report

    monkeypatch.delenv("ARCHIV_HOME", raising=False)
    remember_home(tmp_path / "gone")
    report = doctor_report()
    check = next(c for c in report["checks"] if c["name"] == "remembered_library")
    assert not check["passed"] and "is missing" in check["detail"]
    assert report["status"] == "failed"


def _button(widget: Misc, text: str) -> ttk.Button | None:
    from tkinter import ttk

    for child in widget.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            return child
        if (hit := _button(child, text)) is not None:
            return hit
    return None


def test_settings_keeps_the_saved_model_and_checks_it_off_the_window_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product.configure_model(tmp_path, "http://127.0.0.1:8080", "mine")
    tkinter = pytest.importorskip("tkinter")
    tk_product = pytest.importorskip("archiv.ui.tk_product")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    detected = (("http://127.0.0.1:11434", "Ollama", "other"),)
    monkeypatch.setattr(tk_product, "detect_loopback_models", lambda: detected)
    desktop = tk_product.ProductApp(root, home=tmp_path)
    checked: list[Path] = []

    def answered(home: Path) -> tuple[bool, str]:
        checked.append(home)
        return True, "mine answered."

    monkeypatch.setattr(tk_product, "verify_model", answered)
    try:
        desktop._show("Settings")  # pyright: ignore[reportPrivateUsage]
        button = _button(desktop.body, "Use this model")
        assert button is not None
        button.invoke()
        for _ in range(50):
            root.update()
            if desktop.status.get().startswith("Model ready"):
                break
            root.after(20)
        config = load_model_config(tmp_path)
        assert (config.endpoint, config.model) == ("http://127.0.0.1:8080", "mine")
        assert checked == [tmp_path]
        assert desktop.status.get() == "Model ready: mine answered."
    finally:
        root.destroy()
