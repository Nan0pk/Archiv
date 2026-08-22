"""Derive a versioned console schema from the real Typer command tree.

The schema is produced by converting the installed :data:`archiv.cli.app`
Typer application into its command representation and walking the resulting
groups recursively.  No command metadata is duplicated by hand: renaming,
adding, or removing a CLI command changes the console automatically.

Typer releases between 0.12 and 1.0 expose commands either as real Click
objects or as Typer's vendored command objects, so the walker intentionally
detects capabilities structurally instead of relying on one concrete class
hierarchy.
"""

from __future__ import annotations

import enum
from typing import Any, cast

import typer
from pydantic import Field
from typer.main import get_command

from archiv import __version__
from archiv.contracts import StrictModel

CONSOLE_SCHEMA_VERSION = "1"

_TOOL_OPTIONS = frozenset({"--help", "--install-completion", "--show-completion"})
_COMPLETION_PARAMETERS = frozenset({"install_completion", "show_completion", "help"})


class ParameterKind(enum.StrEnum):
    """UI control families the console knows how to render and validate."""

    FILE = "file"
    FOLDER = "folder"
    PATH = "path"
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    CHOICE = "choice"
    BOOLEAN = "boolean"


class ConsoleParameter(StrictModel):
    """One CLI argument or option as the console must present it."""

    schema_version: str = "1"
    parameter: str = Field(min_length=1)
    flag_names: tuple[str, ...] = ()
    is_argument: bool
    kind: ParameterKind
    required: bool
    default: str | None = None
    help: str = ""
    choices: tuple[str, ...] = ()
    integer_min: int | None = None
    integer_max: int | None = None
    must_exist: bool = False
    boolean_off_flag: str | None = None
    boolean_flag_only: bool = False


class ConsoleCommand(StrictModel):
    """One runnable leaf command in the console."""

    schema_version: str = "1"
    path: str = Field(min_length=1)
    argv_prefix: tuple[str, ...] = Field(min_length=1)
    summary: str = ""
    parameters: tuple[ConsoleParameter, ...] = ()


class ConsoleSchema(StrictModel):
    """Versioned snapshot of everything the console can run."""

    schema_version: str = CONSOLE_SCHEMA_VERSION
    archiv_version: str
    commands: tuple[ConsoleCommand, ...] = Field(min_length=1)

    def command_for(self, path: str) -> ConsoleCommand:
        for command in self.commands:
            if command.path == path:
                return command
        raise KeyError(f"unknown console command path: {path}")


def _group_commands(node: Any) -> dict[str, Any] | None:
    """Return child commands for Click groups and Typer groups alike."""

    commands = cast(object, getattr(node, "commands", None))
    if not isinstance(commands, dict):
        return None
    names = cast(dict[object, object], commands)
    if all(isinstance(name, str) for name in names):
        return cast(dict[str, Any], commands)
    return None


def _parameter_kind(parameter: Any) -> ParameterKind:
    param_type = getattr(parameter, "type", None)
    type_name = type(param_type).__name__
    if hasattr(param_type, "dir_okay") and hasattr(param_type, "file_okay"):
        dir_okay = bool(getattr(param_type, "dir_okay", False))
        file_okay = bool(getattr(param_type, "file_okay", True))
        if dir_okay and file_okay:
            return ParameterKind.PATH
        if dir_okay:
            return ParameterKind.FOLDER
        return ParameterKind.FILE
    if type_name in {"IntRange", "Int", "IntParamType"}:
        return ParameterKind.INTEGER
    if type_name in {"Float", "FloatRange", "FloatParamType"}:
        return ParameterKind.NUMBER
    if isinstance(getattr(param_type, "choices", None), (list, tuple)):
        return ParameterKind.CHOICE
    if bool(getattr(parameter, "is_bool_flag", False)) or type_name in {
        "Bool",
        "BoolParamType",
    }:
        return ParameterKind.BOOLEAN
    return ParameterKind.TEXT


def _default_text(parameter: Any) -> str | None:
    default = getattr(parameter, "default", None)
    if default is None:
        return None
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, int | float):
        return repr(default)
    if isinstance(default, str):
        return default
    return str(default)


def _boolean_off_flag(parameter: Any) -> str | None:
    if not getattr(parameter, "is_bool_flag", False):
        return None
    primary = tuple(getattr(parameter, "opts", ()) or ())
    secondary = tuple(getattr(parameter, "secondary_opts", ()) or ())
    for name in secondary:
        if name.startswith("--no-"):
            return name
    for name in secondary:
        if name not in primary:
            return name
    return None


def _integer_range(parameter: Any) -> tuple[int | None, int | None]:
    param_type = getattr(parameter, "type", None)
    lower = getattr(param_type, "min", None)
    upper = getattr(param_type, "max", None)
    lower_value = lower if isinstance(lower, int) else None
    upper_value = upper if isinstance(upper, int) else None
    return lower_value, upper_value


def _convert_parameter(parameter: Any) -> ConsoleParameter | None:
    name = getattr(parameter, "name", None)
    if not isinstance(name, str) or not name:
        return None
    opts = tuple(getattr(parameter, "opts", ()) or ())
    is_option = any(opt.startswith("-") for opt in opts)
    is_argument = not is_option
    flag_names = opts if is_option else ()
    if is_option and all(opt in _TOOL_OPTIONS for opt in flag_names):
        return None
    if bool(getattr(parameter, "is_eager", False)) and name in _COMPLETION_PARAMETERS:
        return None
    if name in _COMPLETION_PARAMETERS and is_argument:
        return None
    kind = _parameter_kind(parameter)
    choices: tuple[str, ...] = ()
    raw_choices = cast(
        object, getattr(cast(object, getattr(parameter, "type", None)), "choices", None)
    )
    if isinstance(raw_choices, list | tuple):
        sequence = cast(list[object] | tuple[object, ...], raw_choices)
        choices = tuple(str(choice) for choice in sequence)
    integer_min, integer_max = _integer_range(parameter)
    must_exist = bool(getattr(getattr(parameter, "type", None), "exists", False))
    off_flag = _boolean_off_flag(parameter)
    flag_only = (
        kind == ParameterKind.BOOLEAN
        and not is_argument
        and bool(getattr(parameter, "is_flag", False))
        and off_flag is None
    )
    return ConsoleParameter(
        parameter=name,
        flag_names=flag_names,
        is_argument=is_argument,
        kind=kind,
        required=bool(getattr(parameter, "required", False)),
        default=_default_text(parameter),
        help=str(getattr(parameter, "help", "") or ""),
        choices=choices,
        integer_min=integer_min,
        integer_max=integer_max,
        must_exist=must_exist,
        boolean_off_flag=off_flag,
        boolean_flag_only=flag_only,
    )


def _walk_group(group: Any, prefix: tuple[str, ...], sink: list[ConsoleCommand]) -> None:
    children = _group_commands(group)
    if children is None:
        return
    for name in sorted(children):
        command = children[name]
        if bool(getattr(command, "hidden", False)):
            continue
        command_path = prefix + (name,)
        if _group_commands(command) is not None:
            _walk_group(command, command_path, sink)
            continue
        parameters = [
            converted
            for parameter in list(getattr(command, "params", []) or [])
            if (converted := _convert_parameter(parameter)) is not None
        ]
        summary = (
            getattr(command, "help", None) or getattr(command, "short_help", None) or ""
        ).strip()
        sink.append(
            ConsoleCommand(
                path=" ".join(command_path),
                argv_prefix=command_path,
                summary=summary.splitlines()[0] if summary else "",
                parameters=tuple(parameters),
            )
        )


def collect_console_schema(app: typer.Typer) -> ConsoleSchema:
    """Build the console schema directly from the installed Typer app."""

    root = get_command(app)
    if _group_commands(root) is None:
        raise TypeError("the Archiv CLI root must remain a command group")
    commands: list[ConsoleCommand] = []
    _walk_group(root, (), commands)
    if not commands:
        raise ValueError("the Archiv CLI exposes no runnable commands")
    return ConsoleSchema(archiv_version=__version__, commands=tuple(commands))
