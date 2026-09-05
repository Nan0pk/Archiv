"""Opt-in policy and configuration for biometric face data (GDPR / BIPA compliance)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from archiv.faces.contracts import FaceConfig
from archiv.storage.layout import ArchivLayout


class BiometricsDisabledError(RuntimeError):
    """Raised when face detection or clustering is attempted without explicit opt-in."""


def face_config_path(layout: ArchivLayout) -> Path:
    """Return path to faces.json configuration file."""
    return layout.config / "faces.json"


def load_face_config(home: Path | None = None) -> FaceConfig:
    """Load face configuration. Defaults to opt_in=False."""
    layout = ArchivLayout.resolve(home)
    config_file = face_config_path(layout)

    # Check environment override
    env_val = os.environ.get("ARCHIV_FACES_OPT_IN", "").strip().lower()
    if env_val in {"1", "true", "yes", "on"}:
        return FaceConfig(opt_in=True, opt_in_at=datetime.now(UTC).isoformat())

    if not config_file.is_file():
        return FaceConfig(opt_in=False)

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
        return FaceConfig.model_validate(data)
    except Exception:
        return FaceConfig(opt_in=False)


def save_face_config(config: FaceConfig, home: Path | None = None) -> None:
    """Save face configuration to disk."""
    layout = ArchivLayout.resolve(home)
    layout.config.mkdir(parents=True, exist_ok=True)
    config_file = face_config_path(layout)
    config_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")


def check_faces_opt_in(home: Path | None = None) -> FaceConfig:
    """Ensure biometric opt-in is active; raise BiometricsDisabledError otherwise."""
    config = load_face_config(home)
    if not config.opt_in:
        raise BiometricsDisabledError(
            "Face detection and clustering is disabled by default (biometric data protection). "
            "To enable, run: archiv faces opt-in (or set ARCHIV_FACES_OPT_IN=1)."
        )
    return config
