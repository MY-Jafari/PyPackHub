"""Presentation helpers for the UI.

These are the little conversions a view needs and nothing else does: turning a
byte count into something a person reads, turning a version into a sortable
key, and reading the progress a package manager reports out of its own output
lines. They are pure functions so they can be tested without a display server.
"""

from __future__ import annotations

import re
from typing import Final

from packaging.version import InvalidVersion, Version

__all__ = [
    "UNKNOWN_TEXT",
    "format_bytes",
    "format_count",
    "parse_progress_percent",
    "version_sort_key",
]

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


#: One percentage the way a package manager prints it, whether as the current
#: rich-style residue ("  ━━╸  45%  2.3/5.1 MB") or the older bracket bar
#: ("[====>     ]  45%"). A percent sign followed by two hexadecimal
#: characters is URL encoding in a wheel file name, not progress - a line
#: like "Downloading pkg-2.0%2B1-py3-none-any.whl" must not move the bar.
_PROGRESS_PERCENT: Final = re.compile(r"(\d{1,3})\s*%(?![0-9A-Fa-f]{2})")


def parse_progress_percent(line: str) -> int | None:
    """The progress one package-manager output line reports, if any.

    pip's download progress is the percentage the log already shows; this
    reads it back out so the run's progress bar can follow it. A manager
    that reports no percentage (uv piped to a pipe, an ordinary message
    line) answers ``None``, and the bar stays honestly animated instead of
    inventing progress.

    One line can carry several redraws when the manager separates them with
    carriage returns inside a single chunk; the last is the newest, so that
    is the one returned.
    """
    newest: str | None = None
    for match in _PROGRESS_PERCENT.findall(line):
        newest = match

    if newest is None:
        return None

    value = int(newest)
    return value if value <= 100 else None
