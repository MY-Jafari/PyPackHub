"""Layout direction management for PyPackHub's bilingual UI.

This module is the small bridge between the language setting in
:mod:`pypackhub.core.settings` and Qt's layout direction. It exists so the
section 3 window shell can ask "should the UI be laid out right-to-left right
now?" without knowing anything about Qt internals and without pulling Qt into the
translation package.

The direction manager is deliberately stateless and pure. The window shell owns
the side that actually calls ``QApplication.setLayoutDirection``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pypackhub.core.settings import Language

__all__ = [
    "Direction",
    "RtlState",
    "resolved_direction",
    "system_locale_is_rtl",
]


class Direction(str, Enum):
    LEFT_TO_RIGHT = "ltr"
    RIGHT_TO_LEFT = "rtl"
    INHERIT = "inherit"


@dataclass(slots=True)
class RtlState:
    """Whether the UI should be laid out right-to-left."""

    direction: Direction
    language: Language
    explicit_rtl: bool = False
    heuristic: str = "language"


def resolved_direction(language: Language) -> RtlState:
    """Return the layout direction implied by the current language setting."""
    if language is Language.FA:
        return RtlState(
            direction=Direction.RIGHT_TO_LEFT,
            language=language,
            explicit_rtl=True,
            heuristic="language",
        )

    if language is Language.SYSTEM:
        if system_locale_is_rtl():
            return RtlState(
                direction=Direction.RIGHT_TO_LEFT,
                language=language,
                explicit_rtl=True,
                heuristic="system",
            )

        return RtlState(
            direction=Direction.LEFT_TO_RIGHT,
            language=language,
            explicit_rtl=False,
            heuristic="system",
        )

    return RtlState(
        direction=Direction.LEFT_TO_RIGHT,
        language=language,
        explicit_rtl=False,
        heuristic="language",
    )


def system_locale_is_rtl() -> bool:
    """Best-effort check for a system locale that reads right-to-left.

    This is intentionally conservative in the section 3 skeleton. A real
    implementation can ask the OS for the locale's script and mapping; here we
    keep it simple and trustworthy rather than trying to guess from the current
    environment.
    """
    import os

    locale = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    # Common Persian locales are fa, fa-IR and aliases like per.
    lowered = locale.lower()
    if lowered.startswith("fa") or lowered.startswith("per-"):
        return True

    # A handful of Arabic locales are also RTL; keep the check narrow and stable.
    return bool(lowered.startswith("ar"))
