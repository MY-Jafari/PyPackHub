"""Main window shell state for PyPackHub.

This module owns the parts of the window that are *state* rather than layout:
which settings are persisted where, which theme is active, which language the UI
renders in, and which layout direction that implies. The widget that draws the
window lives in :mod:`pypackhub.ui.views.main_window`.

Two layers are deliberately separate:

* :class:`SettingsPersistence` is pure Python. It turns a plain dictionary into an
  :class:`~pypackhub.core.settings.AppSettings` and back, so it can be tested
  without Qt and so the parsing rules (a bool written as ``"0"``, a width written
  as a string) live in one obvious place.
* :class:`QSettingsStore` is the thin Qt side: it reads and writes that dictionary
  through ``QSettings`` in the platformdirs data directory.

:class:`WindowShell` glues them to the theme manager and the translator. It is
the object the window asks for "what should I render?" and the object that
persists a change. It touches Qt only in :meth:`WindowShell.apply_to`, which is
given the application or widget to style, so the rest of the shell stays
testable and headless.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar

from pypackhub.core.settings import (
    AppSettings,
    ColorTheme,
    Language,
    settings_file,
    validate_settings,
)
from pypackhub.ui.i18n.direction import Direction, RtlState, resolved_direction
from pypackhub.ui.i18n.translator import Translator, resolve_language
from pypackhub.ui.theme import ThemeName
from pypackhub.ui.theme.design_tokens import FONT_FAMILIES
from pypackhub.ui.theme.theme_manager import ActiveTheme, ThemeManager, system_variant

if TYPE_CHECKING:  # pragma: no cover - only needed by the type checker
    from collections.abc import Mapping
    from pathlib import Path

E = TypeVar("E")

#: Defaults for keys that may be absent from a stored file. They are literals
#: rather than ``AppSettings.<field>`` because a slots dataclass replaces its
#: class attributes with slot descriptors, so the class attribute is not the
#: default value.
DEFAULT_SIDEBAR_WIDTH = 220
DEFAULT_PACKAGE_PAGE_SIZE = 60
DEFAULT_PYPI_INDEX_URL = "https://pypi.org/simple"

__all__ = [
    "QSettingsStore",
    "SettingsPersistence",
    "SettingsPersistenceError",
    "WindowShell",
]

#: Persian UI text is set in Vazirmatn; the rest in Inter. Both are bundled.
RTL_FONT_FAMILY = "Vazirmatn"
LTR_FONT_FAMILY = "Inter"


class SettingsPersistenceError(RuntimeError):
    """The QSettings bridge failed to read or write the settings."""


def _as_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return int(value)
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return float(value)
    return default


def _as_str_list(value: object) -> tuple[str, ...]:
    """Read a list of strings written either as a list or as one-per-line text.

    ``QSettings`` hands an INI list back as a ``list``, but a value a user edited
    by hand can arrive as a single string with newline separators; both are
    accepted, and blanks are dropped.
    """
    if isinstance(value, str):
        return tuple(item.strip() for item in value.splitlines() if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _as_enum(enum_type: type[E], value: object, default: E) -> E:
    """Read an enum member by value, tolerating junk from an edited file."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return enum_type(value)  # type: ignore[call-arg]
    return default


@dataclass(slots=True)
class SettingsPersistence:
    """Read and write :class:`AppSettings` through a plain dictionary.

    The dictionary is what ``QSettings`` stores. Keeping the translation here
    means the parsing rules are testable without Qt and the window never has to
    know how a bool gets written to an INI file.
    """

    def read(self, values: Mapping[str, object]) -> AppSettings:
        """Turn a settings snapshot into an :class:`AppSettings`."""
        settings = AppSettings(
            theme=_as_enum(ColorTheme, values.get("theme"), ColorTheme.SYSTEM),
            language=_as_enum(Language, values.get("language"), Language.SYSTEM),
            main_window_geometry=_as_str(values.get("main_window_geometry"), ""),
            main_window_state=_as_str(values.get("main_window_state"), ""),
            sidebar_width=_as_int(values.get("sidebar_width"), DEFAULT_SIDEBAR_WIDTH),
            package_list_page_size=_as_int(
                values.get("package_page_size"), DEFAULT_PACKAGE_PAGE_SIZE
            ),
            default_environment_index=_as_int(values.get("default_environment_index"), 0),
            default_target_environment=_as_str(values.get("default_target_environment"), ""),
            pypi_index_url=_as_str(values.get("pypi_index_url"), DEFAULT_PYPI_INDEX_URL),
            extra_index_urls=_as_str_list(values.get("extra_index_urls")),
            trusted_hosts=_as_str_list(values.get("trusted_hosts")),
            auto_refresh_interval_seconds=_as_float(values.get("auto_refresh"), 0.0),
            show_sizes_by_default=_as_bool(values.get("show_sizes"), True),
            show_outdated_by_default=_as_bool(values.get("show_outdated"), True),
            confirm_before_install=_as_bool(values.get("confirm_install"), True),
            confirm_before_uninstall=_as_bool(values.get("confirm_uninstall"), True),
            confirm_before_upgrade=_as_bool(values.get("confirm_upgrade"), True),
            package_manager=_as_str(values.get("package_manager"), "auto"),
        )
        return validate_settings(settings)

    def write(self, settings: AppSettings) -> dict[str, object]:
        """Turn an :class:`AppSettings` into a QSettings-friendly dictionary."""
        return {
            "theme": settings.theme.value,
            "language": settings.language.value,
            "sidebar_width": settings.sidebar_width,
            "package_page_size": settings.package_list_page_size,
            "pypi_index_url": settings.pypi_index_url,
            "extra_index_urls": list(settings.extra_index_urls),
            "trusted_hosts": list(settings.trusted_hosts),
            "auto_refresh": settings.auto_refresh_interval_seconds,
            "show_sizes": settings.show_sizes_by_default,
            "show_outdated": settings.show_outdated_by_default,
            "confirm_install": settings.confirm_before_install,
            "confirm_uninstall": settings.confirm_before_uninstall,
            "confirm_upgrade": settings.confirm_before_upgrade,
            "package_manager": settings.package_manager,
            "default_environment_index": settings.default_environment_index,
            "default_target_environment": settings.default_target_environment,
            "main_window_geometry": settings.main_window_geometry,
            "main_window_state": settings.main_window_state,
        }


class QSettingsStore:
    """Persist the settings dictionary in the platformdirs data directory.

    An INI file inside the application's own data directory is used instead of
    the platform-native registry or plist so that the location matches the one
    :mod:`pypackhub.core.settings` already documents, and so that the file can be
    given user-only permissions where the OS supports it (SPEC section 11.6).
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or settings_file()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, object]:
        """Read every stored key, or an empty mapping when there is no file."""
        from PySide6.QtCore import QSettings

        try:
            settings = QSettings(str(self._path), QSettings.Format.IniFormat)
            return {key: settings.value(key) for key in settings.allKeys()}
        except OSError as error:  # pragma: no cover - depends on the filesystem
            raise SettingsPersistenceError(f"could not read {self._path}: {error}") from error

    def save(self, values: Mapping[str, object]) -> None:
        """Write the mapping, replacing whatever was there before."""
        from PySide6.QtCore import QSettings

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            settings = QSettings(str(self._path), QSettings.Format.IniFormat)
            settings.clear()
            for key, value in values.items():
                settings.setValue(key, value)
            settings.sync()
            if settings.status() != QSettings.Status.NoError:
                raise SettingsPersistenceError(f"could not write {self._path}: {settings.status()}")
        except OSError as error:
            raise SettingsPersistenceError(f"could not write {self._path}: {error}") from error


class WindowShell:
    """The window's settings, theme and language state.

    The window keeps one of these for the lifetime of the session. It answers
    what to render and it persists changes; it never draws anything.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        persistence: SettingsPersistence | None = None,
        store: QSettingsStore | None = None,
    ) -> None:
        self._persistence = persistence or SettingsPersistence()
        self._store = store
        self._settings = validate_settings(settings or AppSettings())
        self._theme_manager = ThemeManager(settings=self._settings)
        self._translator = Translator(language=self._settings.language)
        self._direction_state = resolved_direction(self._settings.language)

    # -- state -----------------------------------------------------------------

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def theme_manager(self) -> ThemeManager:
        return self._theme_manager

    @property
    def active_theme(self) -> ActiveTheme:
        return self._theme_manager.active_theme()

    @property
    def translator(self) -> Translator:
        return self._translator

    @property
    def direction_state(self) -> RtlState:
        return self._direction_state

    @property
    def is_rtl(self) -> bool:
        return self._direction_state.direction is Direction.RIGHT_TO_LEFT

    @property
    def font_family(self) -> str:
        """The family the window should ask Qt for, which follows the script."""
        return RTL_FONT_FAMILY if self.is_rtl else LTR_FONT_FAMILY

    def text(self, key: str, **params: Any) -> str:
        """Translate *key* in the current language, formatted with *params*."""
        return self._translator.translate(key, **params)

    # -- changes ---------------------------------------------------------------

    def apply_settings(self, settings: AppSettings) -> AppSettings:
        """Adopt *settings*, re-resolving the theme, language and direction."""
        validated = validate_settings(settings)
        self._settings = validated
        self._theme_manager = ThemeManager(settings=validated)
        self._translator = Translator(language=validated.language)
        self._direction_state = resolved_direction(validated.language)
        return validated

    def set_theme(self, theme: ColorTheme) -> AppSettings:
        return self.apply_settings(self._theme_manager.set_theme(theme))

    def set_language(self, language: Language) -> AppSettings:
        updated: AppSettings = replace(self._settings, language=language)
        return self.apply_settings(updated)

    def toggle_theme(self) -> AppSettings:
        """Flip light/dark, using the resolved system theme as the flip target."""
        prefer_dark = system_variant() is not ThemeName.DARK
        return self.apply_settings(self._theme_manager.toggle_theme(prefer_dark=prefer_dark))

    def toggle_language(self) -> AppSettings:
        """Flip between English and Persian; from ``SYSTEM`` land on the other."""
        current = resolve_language(self._settings.language)
        next_language = Language.FA if current is Language.EN else Language.EN
        return self.set_language(next_language)

    # -- persistence -----------------------------------------------------------

    def load(self) -> AppSettings:
        """Read the stored settings, if a store was provided."""
        if self._store is None:
            return self._settings
        return self.apply_settings(self._persistence.read(self._store.load()))

    def save(self) -> None:
        """Write the current settings, if a store was provided."""
        if self._store is None:
            return
        self._store.save(self._persistence.write(self._settings))

    # -- Qt --------------------------------------------------------------------

    def apply_to(self, target: object) -> ActiveTheme:
        """Apply the theme, the font and the layout direction to a Qt object.

        *target* is the ``QApplication`` (or any widget that should stand in for
        it). Applying at the application level is what makes both switches live:
        Qt re-resolves the stylesheet and re-mirrors the layouts, so no widget
        needs to know anything changed.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont

        set_stylesheet = getattr(target, "setStyleSheet", None)
        theme = self.active_theme
        if set_stylesheet is not None:
            # The stylesheet depends on the direction too: Qt mirrors the widget
            # order but not a rule that says "border-right".
            set_stylesheet(theme.stylesheet(rtl=self.is_rtl))

        set_direction = getattr(target, "setLayoutDirection", None)
        if set_direction is not None:
            direction = (
                Qt.LayoutDirection.RightToLeft if self.is_rtl else Qt.LayoutDirection.LeftToRight
            )
            set_direction(direction)

        set_font = getattr(target, "setFont", None)
        if set_font is not None:
            font = QFont(FONT_FAMILIES[0])
            font.setFamilies(list(FONT_FAMILIES))
            if self.is_rtl:
                font.setFamilies([RTL_FONT_FAMILY, LTR_FONT_FAMILY])
            set_font(font)

        return theme
