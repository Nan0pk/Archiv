"""Archiv home resolution and durable directory layout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchivLayout:
    """Resolved paths for canonical, derived, index, and database state."""

    root: Path
    originals: Path
    derived: Path
    indexes: Path
    temporary: Path
    database: Path

    @classmethod
    def resolve(cls, explicit: Path | None = None) -> ArchivLayout:
        if explicit is not None:
            root = explicit.expanduser()
        elif value := os.environ.get("ARCHIV_HOME"):
            root = Path(value).expanduser()
        else:
            xdg_data_home = os.environ.get("XDG_DATA_HOME")
            data_home = (
                Path(xdg_data_home).expanduser()
                if xdg_data_home
                else Path.home() / ".local" / "share"
            )
            root = data_home / "archiv"

        root = root.resolve()
        return cls(
            root=root,
            originals=root / "originals" / "sha256",
            derived=root / "derived",
            indexes=root / "indexes",
            temporary=root / "temporary",
            database=root / "archiv.sqlite3",
        )

    def ensure(self) -> None:
        """Create only the storage roots; object-specific paths remain lazy."""

        for path in (self.originals, self.derived, self.indexes, self.temporary):
            path.mkdir(parents=True, exist_ok=True)

    def original_path(self, digest: str) -> Path:
        return self.originals / digest[:2] / digest

    def derived_root(self, digest: str) -> Path:
        return self.derived / digest
