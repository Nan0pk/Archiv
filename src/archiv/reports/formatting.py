"""Human-readable citation and locator formatting."""

from __future__ import annotations

import json


def _render_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def format_locator(locator: dict[str, object]) -> str:
    """Format a native locator without discarding unknown future fields."""

    ordered_keys = (
        "page",
        "paragraph",
        "line",
        "sheet",
        "cell",
        "slide",
        "shape",
        "bounding_box",
        "timestamp_ms",
        "start_ms",
        "end_ms",
        "metadata_chunk",
    )
    parts: list[str] = []
    used: set[str] = set()
    for key in ordered_keys:
        if key in locator:
            parts.append(f"{key.replace('_', ' ')} {_render_value(locator[key])}")
            used.add(key)
    for key in sorted(set(locator) - used):
        parts.append(f"{key.replace('_', ' ')} {_render_value(locator[key])}")
    return ", ".join(parts) if parts else "document"
