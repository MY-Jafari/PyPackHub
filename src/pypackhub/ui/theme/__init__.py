"""Design tokens, QSS themes, and the theme manager for PyPackHub.

This package owns the visual vocabulary of the application: colours, typography,
spacing, elevation, and the stylesheets that switch the whole UI between light
and dark.

* :mod:`pypackhub.ui.theme.design_tokens` is plain data, readable from the core,
  from tests, and from the i18n/RTL layer without importing Qt.
* :mod:`pypackhub.ui.theme.qss` turns tokens into the light and dark stylesheets.
* :mod:`pypackhub.ui.theme.theme_manager` resolves the active theme (including
  "follow the system") and applies it to a live application.

Only the theme manager touches Qt, and it imports PySide6 lazily, so importing
this package stays cheap and headless-safe.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "ThemeName",
]


class ThemeName(str, Enum):
    """The theme a user can ask for. ``SYSTEM`` follows the OS preference."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"
