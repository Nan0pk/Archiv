"""Explicit, recorded consent for one archive to be used with a model on another machine.

Archiv's model interface is loopback-only: nothing leaves the machine. Evaluating the
product against a hosted model breaks that, so it is not something an archive can drift
into. An archive is usable that way only if it carries a mark saying so, and the mark
records what was agreed and when, so the consent can be checked later rather than taken
on trust.

The shape here deliberately copies `archiv.faces.config`, which solves the same problem
for biometric data. Missing file means not enabled. An unreadable or incomplete file
means not enabled. Nothing about this file can turn the feature on by accident.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import model_validator

from archiv.contracts import StrictModel
from archiv.storage.layout import ArchivLayout

ENABLE_COMMAND = "archiv model evaluation enable --acknowledge-documents-leave-this-machine"
"""The exact command a refusal message tells the user to run. Kept here so the message
and the CLI cannot drift apart."""

ENV_OVERRIDE = "ARCHIV_EVALUATION_ARCHIVE"

ACKNOWLEDGEMENT = (
    "I understand that marking this archive for evaluation means the text of its "
    "documents is sent to a model running on computers I do not control, that it "
    "therefore leaves this machine, and that I must not do this to an archive holding "
    "private or confidential material."
)


class EvaluationConfig(StrictModel):
    """Whether this archive is marked for evaluation, and the record of that decision."""

    schema_version: str = "1"
    evaluation: bool = False
    acknowledged_at: str | None = None
    acknowledgement: str | None = None

    @model_validator(mode="after")
    def require_a_recorded_acknowledgement(self) -> EvaluationConfig:
        # A bare `true` is not consent. Refusing it here is what makes a hand-edited
        # file fall back to "not enabled" rather than silently switching the archive on,
        # because the loader below treats an invalid file as not enabled.
        if self.evaluation and not (self.acknowledged_at and self.acknowledgement):
            raise ValueError(
                "an archive marked for evaluation must record when the acknowledgement "
                "was given and what was acknowledged"
            )
        return self


class EvaluationNotEnabledError(RuntimeError):
    """Raised when a model outside this machine is asked for on an unmarked archive."""


def evaluation_config_path(layout: ArchivLayout) -> Path:
    return layout.config / "evaluation.json"


def load_evaluation_config(home: Path | None = None) -> EvaluationConfig:
    """Read the mark. Anything unreadable, absent or incomplete means not enabled."""

    env_value = os.environ.get(ENV_OVERRIDE, "").strip().lower()
    if env_value in {"1", "true", "yes", "on"}:
        return EvaluationConfig(
            evaluation=True,
            acknowledged_at=datetime.now(UTC).isoformat(),
            # Recorded honestly: an environment variable was set, a person did not agree
            # to anything. An audit of this archive should be able to tell the difference.
            acknowledgement=f"enabled for this process by the {ENV_OVERRIDE} environment variable",
        )

    path = evaluation_config_path(ArchivLayout.resolve(home))
    if not path.is_file():
        return EvaluationConfig()
    try:
        return EvaluationConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return EvaluationConfig()


def save_evaluation_config(config: EvaluationConfig, home: Path | None = None) -> Path:
    """Write the mark, replacing the file in one step so a crash cannot leave it half-written."""

    layout = ArchivLayout.resolve(home)
    layout.config.mkdir(parents=True, exist_ok=True)
    path = evaluation_config_path(layout)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def mark_for_evaluation(home: Path | None = None) -> EvaluationConfig:
    """Record that the user accepted `ACKNOWLEDGEMENT` for this archive, and save it."""

    config = EvaluationConfig(
        evaluation=True,
        acknowledged_at=datetime.now(UTC).isoformat(),
        acknowledgement=ACKNOWLEDGEMENT,
    )
    save_evaluation_config(config, home)
    return config


def clear_evaluation_mark(home: Path | None = None) -> EvaluationConfig:
    """Remove the mark. The archive goes back to refusing any model outside this machine."""

    config = EvaluationConfig()
    save_evaluation_config(config, home)
    return config


def check_evaluation_opt_in(home: Path | None = None) -> EvaluationConfig:
    """Return the mark, or refuse with a message saying exactly what to do about it."""

    config = load_evaluation_config(home)
    if not config.evaluation:
        raise EvaluationNotEnabledError(
            "This archive is not marked for evaluation, so Archiv will not send anything "
            "to a model running outside this machine.\n"
            "\n"
            "Marking it means the text of documents in this archive is sent to computers "
            "you do not control. Do not do this to an archive that holds private or "
            "confidential documents; make a separate archive for evaluation instead.\n"
            "\n"
            f"To mark this archive, run:\n    {ENABLE_COMMAND}\n"
            f"For automated tests only, setting {ENV_OVERRIDE}=1 has the same effect for "
            "one process and is recorded as such."
        )
    return config
