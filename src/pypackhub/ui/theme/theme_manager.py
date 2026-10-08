"""Theme manager for PyPackHub.

This module is the bridge between the design tokens in
:mod:`pypackhub.ui.theme.design_tokens`, the stylesheet builder in
:mod:`pypackhub.ui.theme.qss`, and the application's settings.

It answers three questions:

* which tokens are active right now (:func:`tokens_for_settings`);
* what does "follow the system theme" mean on this machine (:func:`system_variant`,
  which asks Qt for the operating system preference);
* which stylesheet those tokens produce (:meth:`ThemeManager.stylesheet`).

Applying the stylesheet is the window shell's job, because the sheet depends on
whether the layout is mirrored and Qt is the shell's business.

Everything except the one Qt call is pure, so the token resolution can be tested
without a display server and that call is imported lazily, inside the function
that needs it. Importing this module does not import PySide6.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pypackhub.core.settings import AppSettings, ColorTheme
from pypackhub.ui.theme import ThemeName
from pypackhub.ui.theme.design_tokens import (
    DesignTokens,
    ThemeVariant,
    default_dark_tokens,
    default_light_tokens,
)
from pypackhub.ui.theme.qss import build_qss

__all__ = [
    "ActiveTheme",
    "ThemeManager",
    "system_variant",
    "tokens_for_settings",
]


@dataclass(slots=True)
class ActiveTheme:
    """The theme the UI should currently render with."""

    name: ThemeName
    variant: ThemeVariant
    tokens: DesignTokens
    #: Set when the requested theme was ``SYSTEM``: records what the operating
    #: system preference resolved to, so the UI can explain the choice.
    resolved_system_theme: ThemeName | None = None

    def stylesheet(self, *, rtl: bool = False) -> str:
        """The Qt stylesheet for this theme and layout direction."""
        return build_qss(self.tokens, rtl=rtl)


def _variant_for_theme(theme: ThemeName) -> ThemeVariant:
    return ThemeVariant.DARK if theme is ThemeName.DARK else ThemeVariant.LIGHT


def tokens_for_settings(settings: AppSettings) -> ActiveTheme:
    """Return the active theme derived from the current settings object."""
    requested = settings.theme
    resolved_system_theme: ThemeName | None = None

    if requested is ColorTheme.DARK:
        name = ThemeName.DARK
    elif requested is ColorTheme.LIGHT:
        name = ThemeName.LIGHT
    else:
        name = system_variant()
        resolved_system_theme = name

    variant = _variant_for_theme(name)
    tokens: DesignTokens = (
        default_dark_tokens() if variant is ThemeVariant.DARK else default_light_tokens()
    )

    return ActiveTheme(
        name=name,
        variant=variant,
        tokens=tokens,
        resolved_system_theme=resolved_system_theme,
    )


def system_variant() -> ThemeName:
    """Resolve ``SYSTEM`` to a concrete variant.

    Qt 6.5 and later report the operating system's colour scheme through
    ``QGuiApplication.styleHints().colorScheme()``. When there is no running
    application (a headless test, the CLI, an import before ``QApplication``
    exists) or the platform reports "unknown", this falls back to light, which is
    the same default the rest of the application starts from.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
    except ImportError:  # pragma: no cover - Qt is a hard runtime dependency
        return ThemeName.LIGHT

    application = QGuiApplication.instance()
    if application is None:
        return ThemeName.LIGHT

    # ``getattr`` rather than an attribute access: the stub for ``instance()``
    # types the result as a plain ``QCoreApplication``, which has no
    # ``styleHints``, and a Qt older than 6.5 does not have it either.
    style_hints = getattr(application, "styleHints", None)
    if style_hints is None:  # pragma: no cover - very old Qt without styleHints
        return ThemeName.LIGHT

    try:
        scheme = style_hints.colorScheme()
    except AttributeError:  # pragma: no cover - defensive
        return ThemeName.LIGHT

    if scheme is Qt.ColorScheme.Dark:
        return ThemeName.DARK
    return ThemeName.LIGHT


class ThemeManager:
    """A small theme manager on top of the settings model.

    Create one per session. :meth:`active_theme` answers which tokens to render
    with; :meth:`apply` pushes them at a live Qt application;
    :meth:`set_theme` returns an updated settings object for the caller to
    persist.
    """

    def __init__(self, *, settings: AppSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def active_theme(self) -> ActiveTheme:
        return tokens_for_settings(self._settings)

    def stylesheet(self, *, rtl: bool = False) -> str:
        """The stylesheet for the active theme and the current layout direction.

        Applying it is the window shell's job, not this module's: the shell is
        what owns Qt and knows whether the UI is mirrored.
        """
        return self.active_theme().stylesheet(rtl=rtl)

    def set_theme(self, theme: ColorTheme) -> AppSettings:
        """Return a copy of the settings with the requested theme applied."""
        updated: AppSettings = replace(self._settings, theme=theme)
        return updated

    def toggle_theme(self, *, prefer_dark: bool | None = None) -> AppSettings:
        """Flip between light and dark.

        A ``SYSTEM`` theme has no opposite, so the caller's ``prefer_dark`` hint
        (usually the current system preference) decides which side to land on;
        without a hint the flip goes to light.
        """
        current = self._settings.theme
        if current is ColorTheme.DARK:
            next_theme = ColorTheme.LIGHT
        elif current is ColorTheme.LIGHT:
            next_theme = ColorTheme.DARK
        elif prefer_dark is not None:
            next_theme = ColorTheme.DARK if prefer_dark else ColorTheme.LIGHT
        else:
            next_theme = ColorTheme.LIGHT

        return self.set_theme(next_theme)
