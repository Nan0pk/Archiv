"""Shared errors for the local test console."""

from __future__ import annotations


class UiError(RuntimeError):
    """The test console cannot complete an operation safely."""
