"""Map console schema parameters onto plain control descriptions.

The description layer is deliberately widget-free so the console's behavior
is fully testable without a display: the Tk layer only renders what this
module decides.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from archiv.ui.console_schema import ConsoleCommand, ConsoleParameter, ParameterKind


class BrowseMode(enum.StrEnum):
    """Native picker offered next to a path control."""

    NONE = "none"
    FILE = "file"
    FOLDER = "folder"
    FILE_OR_FOLDER = "file_or_folder"


@dataclass(frozen=True)
class ControlPlan:
    """Everything a renderer needs for one parameter."""

    parameter: ConsoleParameter
    label: str
    required: bool
    initial_text: str
    initial_checked: bool
    browse: BrowseMode
    choices: tuple[str, ...]
    spinbox_min: int | None
    spinbox_max: int | None

    @property
    def is_checkbox(self) -> bool:
        return self.parameter.kind is ParameterKind.BOOLEAN

    @property
    def is_choice(self) -> bool:
        return self.parameter.kind is ParameterKind.CHOICE


def _browse_mode(parameter: ConsoleParameter) -> BrowseMode:
    if parameter.kind is ParameterKind.FILE:
        return BrowseMode.FILE
    if parameter.kind is ParameterKind.FOLDER:
        return BrowseMode.FOLDER
    if parameter.kind is ParameterKind.PATH:
        return BrowseMode.FILE_OR_FOLDER
    return BrowseMode.NONE


def control_plan(command: ConsoleCommand) -> list[ControlPlan]:
    """Produce the ordered control plan for one console command."""

    plans: list[ControlPlan] = []
    for parameter in command.parameters:
        if parameter.kind is ParameterKind.BOOLEAN:
            initial_text = ""
            initial_checked = parameter.default == "true"
        else:
            initial_text = parameter.default or ""
            initial_checked = False
        flag_label = parameter.flag_names[0] if parameter.flag_names else parameter.parameter
        plans.append(
            ControlPlan(
                parameter=parameter,
                label=flag_label,
                required=parameter.required,
                initial_text=initial_text,
                initial_checked=initial_checked,
                browse=_browse_mode(parameter),
                choices=parameter.choices,
                spinbox_min=parameter.integer_min,
                spinbox_max=parameter.integer_max,
            )
        )
    return plans
