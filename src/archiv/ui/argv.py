"""Build safe argument vectors from console form values.

Values travel from the form straight into one list of strings.  The console
never concatenates a command line and never asks a shell to interpret it, so
spaces, quotes, glob characters, or command substitutions in a value remain
literal data inside one argument vector element.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence

from archiv.ui.console_schema import ConsoleCommand, ConsoleParameter, ParameterKind
from archiv.ui.errors import UiError

MAX_VALUE_CHARACTERS = 8192


def _check_text(parameter: ConsoleParameter, value: object) -> str:
    if not isinstance(value, str):
        raise UiError(f"parameter {parameter.parameter!r} expects text")
    if "\x00" in value:
        raise UiError(f"parameter {parameter.parameter!r} must not contain NUL bytes")
    if len(value) > MAX_VALUE_CHARACTERS:
        raise UiError(
            f"parameter {parameter.parameter!r} exceeds {MAX_VALUE_CHARACTERS} characters"
        )
    return value


def _check_integer(parameter: ConsoleParameter, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise UiError(f"parameter {parameter.parameter!r} expects an integer")
    try:
        number = int(str(value).strip(), 10)
    except ValueError as error:
        raise UiError(f"parameter {parameter.parameter!r} expects an integer") from error
    if parameter.integer_min is not None and number < parameter.integer_min:
        raise UiError(f"parameter {parameter.parameter!r} must be at least {parameter.integer_min}")
    if parameter.integer_max is not None and number > parameter.integer_max:
        raise UiError(f"parameter {parameter.parameter!r} must be at most {parameter.integer_max}")
    return str(number)


def _check_number(parameter: ConsoleParameter, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise UiError(f"parameter {parameter.parameter!r} expects a number")
    try:
        number = float(str(value).strip())
    except ValueError as error:
        raise UiError(f"parameter {parameter.parameter!r} expects a number") from error
    return repr(number)


def _check_choice(parameter: ConsoleParameter, value: object) -> str:
    text = _check_text(parameter, value)
    if text not in parameter.choices:
        allowed = ", ".join(parameter.choices) or "none"
        raise UiError(f"parameter {parameter.parameter!r} must be one of: {allowed}")
    return text


def _truthy(parameter: ConsoleParameter, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    raise UiError(f"parameter {parameter.parameter!r} expects a boolean")


def _matches_schema_default(parameter: ConsoleParameter, value: object) -> bool:
    """True when a touched value restates the CLI default and can be omitted."""

    default = parameter.default
    if default is None:
        return False
    if parameter.kind is ParameterKind.BOOLEAN:
        try:
            return _truthy(parameter, value) == (default.strip().lower() == "true")
        except UiError:
            return False
    if isinstance(value, str):
        return value.strip() == default.strip()
    return str(value) == default


def build_argv(command: ConsoleCommand, values: Mapping[str, object] | None = None) -> list[str]:
    """Return the full argv prefix plus encoded parameters for one command.

    Values equal to the CLI default are omitted so the displayed invocation
    stays minimal and equivalent.  Unknown parameter names, missing required
    values, out-of-range numbers, and undeclared choices all fail with a
    clear :class:`UiError` before anything runs.
    """

    provided = dict(values or {})
    declared = {parameter.parameter: parameter for parameter in command.parameters}
    unknown = sorted(set(provided) - set(declared))
    if unknown:
        raise UiError("unknown parameter(s) for this command: " + ", ".join(sorted(unknown)))

    option_parts: list[str] = []
    argument_parts: list[str] = []
    for parameter in command.parameters:
        if parameter.parameter not in provided:
            if (
                parameter.required
                and parameter.default is None
                and parameter.kind is not ParameterKind.BOOLEAN
            ):
                raise UiError(f"missing required parameter: {parameter.parameter}")
            continue
        raw: object = provided[parameter.parameter]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if (
                parameter.required
                and parameter.default is None
                and parameter.kind is not ParameterKind.BOOLEAN
            ):
                raise UiError(f"missing required parameter: {parameter.parameter}")
            continue
        if not parameter.required and _matches_schema_default(parameter, raw):
            continue
        if parameter.kind is ParameterKind.BOOLEAN:
            enabled = _truthy(parameter, raw)
            if parameter.is_argument:
                raise UiError(f"parameter {parameter.parameter!r} cannot be a boolean argument")
            flag = parameter.flag_names[0]
            if parameter.boolean_flag_only:
                if enabled:
                    option_parts.append(flag)
                continue
            if enabled:
                option_parts.append(flag)
            elif parameter.boolean_off_flag is not None:
                option_parts.append(parameter.boolean_off_flag)
            continue
        if parameter.kind is ParameterKind.INTEGER:
            text = _check_integer(parameter, raw)
        elif parameter.kind is ParameterKind.NUMBER:
            text = _check_number(parameter, raw)
        elif parameter.kind is ParameterKind.CHOICE:
            text = _check_choice(parameter, raw)
        else:
            text = _check_text(parameter, raw)
        if parameter.is_argument:
            argument_parts.append(text)
        else:
            option_parts.extend((parameter.flag_names[0], text))

    return [*command.argv_prefix, *option_parts, *argument_parts]


def quoted_invocation(argv: Sequence[str], *, executable: str = "archiv") -> str:
    """Render an argv vector for display only.

    The output is a faithful readable equivalent of the executed argument
    vector; it is never parsed back or executed by the console.
    """

    return " ".join(shlex.quote(part) for part in [executable, *argv])
