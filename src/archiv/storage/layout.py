"""Archiv home resolution and durable directory layout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LAYOUT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ArchivLayout:
    """Resolved paths for canonical, derived, index, and database state."""

    root: Path
    originals: Path
    derived: Path
    indexes: Path
    temporary: Path
    runs: Path
    outputs: Path
    config: Path
    database: Path

    @property
    def version_file(self) -> Path:
        return self.root / "layout-version"

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
            runs=root / "runs",
            outputs=root / "outputs",
            config=root / "config",
            database=root / "archiv.sqlite3",
        )

    def ensure(self) -> None:
        """Create only the storage roots; object-specific paths remain lazy."""

        for path in (
            self.originals,
            self.derived,
            self.indexes,
            self.temporary,
            self.runs,
            self.outputs,
            self.config,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.version_file.exists():
            try:
                found = int(self.version_file.read_text(encoding="ascii").strip())
            except ValueError as error:
                raise RuntimeError("ARCHIV_HOME layout-version is invalid") from error
            if found > LAYOUT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"ARCHIV_HOME layout {found} is newer than supported layout "
                    f"{LAYOUT_SCHEMA_VERSION}; upgrade Archiv or restore a compatible backup"
                )
        else:
            temporary = self.root / ".layout-version.tmp"
            temporary.write_text(f"{LAYOUT_SCHEMA_VERSION}\n", encoding="ascii")
            os.replace(temporary, self.version_file)

    def original_path(self, digest: str) -> Path:
        return self.originals / digest[:2] / digest

    def derived_root(self, digest: str) -> Path:
        return self.derived / digest
