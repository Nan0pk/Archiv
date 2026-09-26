from __future__ import annotations

import shutil
import sys

import pytest

from archiv import doctor


def test_doctor_reports_libreoffice_tesseract_and_tkinter(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/bin/soffice" if name == "soffice" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setitem(sys.modules, "tkinter", None)

    report = doctor.doctor_report()
    advisories = {item["name"]: item for item in report["advisories"]}

    assert set(advisories) == {"libreoffice", "tesseract", "tkinter"}
    assert advisories["libreoffice"]["available"] is True
    assert advisories["tesseract"]["available"] is False
    assert advisories["tkinter"]["available"] is False
    # Advisories never gate the deterministic-minimum status.
    assert report["status"] == "ok"


def test_advisory_rows_name_the_command_they_unblock() -> None:
    report = doctor.doctor_report()
    advisories = {item["name"]: item for item in report["advisories"]}

    assert "archiv report" in advisories["libreoffice"]["unblocks"]
    assert "text" in advisories["tesseract"]["unblocks"]
    assert "archiv ui" in advisories["tkinter"]["unblocks"]
