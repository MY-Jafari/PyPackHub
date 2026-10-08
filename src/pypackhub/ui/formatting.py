"""Presentation helpers for the UI.

These are the little conversions a view needs and nothing else does: turning a
byte count into something a person reads, and turning a version into a sortable
key. They are pure functions so they can be tested without a display server.
"""

from __future__ import annotations

from typing import Final

from packaging.version import InvalidVersion, Version

__all__ = ["UNKNOWN_TEXT", "format_bytes", "format_count", "version_sort_key"]

#: What the table shows where a value is genuinely not known. Deliberately not
#: translatable: it is a placeholder glyph, not a sentence, and translating it
#: would make two different states render identically in a screenshot.
UNKNOWN_TEXT: Final = "\u2014"

_UNITS: Final = ("B", "KiB", "MiB", "GiB", "TiB")


def format_bytes(size_bytes: int | None) -> str:
    """Format a byte count for the size column.

    ``None`` means "not measured", which is different from zero bytes: the table
    must not claim a package takes no space when nobody has looked.
    """
    if size_bytes is None:
        return UNKNOWN_TEXT

    value = float(size_bytes)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} {_UNITS[-1]}"  # pragma: no cover - loop always returns


def format_count(count: int) -> str:
    """Format a count with thousands separators, using the locale's rules."""
    return f"{count:,}"


def version_sort_key(version: str) -> tuple[int, object]:
    """A sort key that puts real versions in order and junk at the end.

    Sorting versions as strings puts ``1.10`` before ``1.9``; sorting them with
    ``packaging`` puts them where a Python user expects. A version that cannot be
    parsed sorts after every valid one, and never raises.
    """
    if not version:
        return (2, "")

    try:
        return (0, Version(version))
    except InvalidVersion:
        return (1, version)
