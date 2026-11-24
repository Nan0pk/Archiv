"""Archiv home resolution and durable directory layout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

LAYOUT_SCHEMA_VERSION = 1


def default_home() -> Path:
    """The library location when nothing else names one."""

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    data_home = (
        Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    )
    return data_home / "archiv"


class MissingLibraryError(RuntimeError):
    """The library remembered by setup is not where it was."""


def home_pointer_path() -> Path:
    """Where ``archiv setup`` records the chosen library, outside the library itself."""

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "archiv" / "home"


def remembered_home() -> Path | None:
    """The library chosen during setup, or None when setup never chose one.

    A remembered library that has gone missing is an error, never a fresh start: an
    unplugged drive must not silently split the user's documents across two libraries.
    """

    pointer = home_pointer_path()
    if not pointer.is_file():
        return None
    root = Path(pointer.read_text(encoding="utf-8").strip()).expanduser()
    if not root.is_dir():
        raise MissingLibraryError(
            f"Archiv's library folder {root} is missing. Reconnect the drive it is on, "
            "or run `archiv setup` to choose another location."
        )
    return root


def remember_home(root: Path) -> Path:
    """Make ``root`` the library every later command uses when none is named."""

    pointer = home_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(f"{root.expanduser().resolve()}\n", encoding="utf-8")
    os.replace(temporary, pointer)
    return pointer


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
        elif (remembered := remembered_home()) is not None:
            root = remembered
        else:
            root = default_home()

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
