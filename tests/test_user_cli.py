from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from archiv.cli import app
from archiv.sample_vault import SAMPLE_FILES

runner = CliRunner()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_everyday_commands_cover_add_find_report_status_and_restore(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sample = runner.invoke(app, ["sample-vault", str(vault)])
    assert sample.exit_code == 0, sample.output
    assert f"Sample vault: {vault}" in sample.output
    vault.joinpath("ignored.bin").write_bytes(b"not a supported document")
    source_hashes = {path.name: _sha256(path) for path in vault.iterdir() if path.is_file()}

    home = tmp_path / "home"
    added = runner.invoke(app, ["add", str(vault), "--home", str(home), "--json"])
    assert added.exit_code == 0, added.output
    add_payload = json.loads(added.output)
    assert len(add_payload["added"]) == len(SAMPLE_FILES)
    assert add_payload["new_originals"] == len(SAMPLE_FILES)
    assert add_payload["duplicates"] == 0
    assert add_payload["skipped_unsupported"] == 2
    assert add_payload["search_index"]["object_count"] == len(SAMPLE_FILES)

    found = runner.invoke(
        app,
        ["find", "unique fixture marker", "--home", str(home)],
    )
    assert found.exit_code == 0, found.output
    assert "Found 3 verified match(es)" in found.output
    for source_name in SAMPLE_FILES:
        assert source_name in found.output

    report = runner.invoke(
        app,
        [
            "report",
            "unique fixture marker",
            "--home",
            str(home),
            "--no-render",
            "--deterministic",
            "--json",
        ],
    )
    assert report.exit_code == 0, report.output
    report_payload = json.loads(report.output)
    assert report_payload["status"] == "succeeded"
    assert report_payload["verification"]["valid"] is True
    assert report_payload["run"]["citation_count"] == len(SAMPLE_FILES)
    assert Path(report_payload["run"]["output_path"]).is_file()

    status = runner.invoke(app, ["status", "--home", str(home), "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["documents"] == len(SAMPLE_FILES)
    assert status_payload["search_index"]["available"] is True
    assert status_payload["reports"]["succeeded"] == 1
    assert status_payload["model"]["adapter"] == "disabled"

    backup_path = tmp_path / "archiv-backup.zip"
    backup = runner.invoke(app, ["backup", str(backup_path), "--home", str(home)])
    assert backup.exit_code == 0, backup.output
    assert f"Backup: {backup_path}" in backup.output

    restored_home = tmp_path / "restored"
    restored = runner.invoke(
        app,
        ["restore", str(backup_path), "--home", str(restored_home)],
    )
    assert restored.exit_code == 0, restored.output
    assert f"Restored: {restored_home}" in restored.output
    assert "Search index rebuilt: yes" in restored.output

    restored_find = runner.invoke(
        app,
        ["find", "unique fixture marker", "--home", str(restored_home)],
    )
    assert restored_find.exit_code == 0, restored_find.output
    assert "Found 3 verified match(es)" in restored_find.output

    assert source_hashes == {path.name: _sha256(path) for path in vault.iterdir() if path.is_file()}


def test_human_defaults_need_no_task_file_or_run_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    runner.invoke(app, ["sample-vault", str(vault)])
    home = tmp_path / "home"

    added = runner.invoke(app, ["add", str(vault), "--home", str(home)])
    assert added.exit_code == 0, added.output
    assert "Added: 3 file(s)" in added.output
    assert "Indexed: 3 document(s)" in added.output

    report = runner.invoke(
        app,
        [
            "report",
            "unique fixture marker",
            "--home",
            str(home),
            "--no-render",
            "--deterministic",
        ],
    )
    assert report.exit_code == 0, report.output
    assert "Report: " in report.output
    assert "Citations: 3" in report.output
    assert "Verified: yes" in report.output
    assert not any((home / "temporary").glob("user-report-*.json"))


def test_report_fails_closed_without_model_or_deterministic_flag(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    runner.invoke(app, ["sample-vault", str(vault)])
    home = tmp_path / "home"
    runner.invoke(app, ["add", str(vault), "--home", str(home)])

    # Without model and without --deterministic: must fail closed and forbid hidden fallback
    res = runner.invoke(
        app,
        [
            "report",
            "unique fixture marker",
            "--home",
            str(home),
            "--no-render",
        ],
    )
    assert res.exit_code != 0
    assert "hidden fallback is forbidden" in res.output


def test_add_persists_failures_so_status_and_diagnostics_can_see_them(tmp_path: Path) -> None:
    """A file that fails ingestion must not vanish from every downstream diagnostic."""

    vault = tmp_path / "vault"
    vault.mkdir()
    vault.joinpath("good.txt").write_text("a readable fixture", encoding="utf-8")
    vault.joinpath("broken.docx").write_bytes(b"not a real docx package")

    home = tmp_path / "home"
    added = runner.invoke(app, ["add", str(vault), "--home", str(home), "--json"])
    assert added.exit_code == 0, added.output
    add_payload = json.loads(added.output)
    assert add_payload["failed"] == 1

    status = runner.invoke(app, ["status", "--home", str(home), "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["ingestions"]["failed"] == 1
    assert status_payload["ingestion_summary"]["failed"] == 1

    from archiv.ui.product import list_documents

    failures = list_documents(home, failures=True)
    assert len(failures) == 1
    assert failures[0].name == "broken.docx"
    assert failures[0].error
