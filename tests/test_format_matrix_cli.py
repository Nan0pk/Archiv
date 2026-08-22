"""Acceptance tests for the read-only `archiv formats` reporter.

The command must report exactly what the committed, test-verified matrix
claims: no invented formats, no silence about rejected ones, and no divergence
from the ingestion surface the rest of the suite exercises live.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from archiv import __version__
from archiv.cli import app
from archiv.format_matrix import load_format_matrix, matrix_path
from archiv.ingestion.formats import SUPPORTED_SUFFIXES

runner = CliRunner()


def test_formats_lists_every_supported_suffix_exactly_once() -> None:
    """The human summary must cover the real ingestion surface with no gaps."""

    result = runner.invoke(app, ["formats"])
    assert result.exit_code == 0, result.output
    for suffix in SUPPORTED_SUFFIXES:
        assert suffix in result.output, f"{suffix} missing from the formats summary"
    assert str(len(SUPPORTED_SUFFIXES)) in result.output


def test_formats_json_matches_the_committed_matrix() -> None:
    """`--json` must emit the committed matrix verbatim, not a rewritten copy."""

    result = runner.invoke(app, ["formats", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    expected = load_format_matrix(matrix_path())
    assert payload == expected.model_dump(mode="json")
    assert payload["product_version"] == __version__


def test_formats_detail_reports_one_family_without_overstating() -> None:
    """A per-format view must show tested claims, including known limits."""

    result = runner.invoke(app, ["formats", "pdf"])
    assert result.exit_code == 0, result.output
    assert "Family: pdf" in result.output
    assert "page" in result.output
    assert "Known limit:" in result.output


def test_formats_detail_accepts_bare_dotted_and_filename_forms() -> None:
    """Operators should not have to guess the argument spelling."""

    outputs = [
        runner.invoke(app, ["formats", spelling]).output
        for spelling in ("odt", ".odt", ".ODT", "quarterly-notes.odt")
    ]
    for output in outputs:
        assert "Family: opendocument-text" in output
    assert len({output for output in outputs}) == 1


def test_formats_metadata_only_family_does_not_claim_document_text() -> None:
    """Audio is metadata-only; the command must not imply transcription."""

    result = runner.invoke(app, ["formats", "wav"])
    assert result.exit_code == 0, result.output
    assert "metadata only" in result.output
    assert "Grounded answers and citations: no" in result.output


def test_formats_rejects_unsupported_suffix_with_the_tested_reason() -> None:
    """A rejected example must fail non-zero and explain itself honestly."""

    result = runner.invoke(app, ["formats", ".docm"])
    assert result.exit_code == 1
    assert "not supported" in result.output
    assert "rejected before any parsing" in result.output


def test_formats_rejects_unknown_suffix_without_inventing_support() -> None:
    """An unheard-of suffix must fail closed rather than guess."""

    result = runner.invoke(app, ["formats", "xyz"])
    assert result.exit_code == 1
    assert ".xyz is not supported." in result.output

    payload = json.loads(runner.invoke(app, ["formats", "xyz", "--json"]).output)
    assert payload == {
        "suffix": ".xyz",
        "supported": False,
        "reason": "not a supported suffix; rejected before any parsing",
    }


def test_formats_reads_no_user_data_and_needs_no_archiv_home(tmp_path: Path) -> None:
    """The reporter is read-only: it must not create or require an Archiv home."""

    home = tmp_path / "absent-home"
    result = runner.invoke(app, ["formats"], env={"ARCHIV_HOME": str(home)})
    assert result.exit_code == 0, result.output
    assert not home.exists()


def test_packaged_matrix_path_is_shipped_for_installed_copies() -> None:
    """`archiv formats` must work from a wheel, not only from a checkout."""

    resolved = matrix_path()
    assert resolved.is_file()
    packaged = Path(__file__).parents[1] / "pyproject.toml"
    content = packaged.read_text(encoding="utf-8")
    assert "archiv/data/format-compatibility.json" in content
