"""Application bootstrap for PyPackHub.

This module is what the ``pypackhub`` console script and ``python -m pypackhub``
call. It is the only place that constructs a ``QApplication``, and it does four
things in order:

1. creates the application with the identifiers :mod:`pypackhub` defines, so
   ``QSettings``, keyring and the log directory agree on one name;
2. registers the bundled fonts (Inter for Latin text, Vazirmatn for Persian,
   JetBrains Mono for logs and versions) so the UI looks the same on every
   machine instead of depending on what is installed;
3. loads the persisted settings through the window shell;
4. builds and shows the main window with the theme already applied, so the first
   frame the user sees is the right one rather than a flash of the wrong theme.

Nothing here decides policy. It wires the pieces together and hands control to
Qt's event loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication

from pypackhub import APP_DISPLAY_NAME, APP_ID, __version__
from pypackhub.core.settings import settings_data_directory
from pypackhub.ui.views.main_window import MainWindow
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell

__all__ = ["build_application", "build_window", "register_bundled_fonts", "run"]

#: Bundled font families, in the order they are registered.
FONT_DIRECTORIES: tuple[str, ...] = ("inter", "vazirmatn", "jetbrains-mono")


def register_bundled_fonts() -> list[str]:
    """Register the fonts shipped with the application.

    Returns the family names Qt actually loaded. A missing font is not fatal -
    Qt falls back to the system family - but the list is returned so a diagnostic
    or a test can tell whether the bundled set was found.
    """
    families: list[str] = []
    fonts_root = Path(__file__).resolve().parents[1] / "assets" / "fonts"

    for directory in FONT_DIRECTORIES:
        for path in sorted((fonts_root / directory).glob("*.ttf")):
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id != -1:
                families.extend(QFontDatabase.applicationFontFamilies(font_id))

    return families


def build_application(argv: list[str] | None = None) -> QApplication:
    """Create the ``QApplication``, or return the one that already exists."""
    existing = QGuiApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    application = QApplication(argv if argv is not None else sys.argv)
    application.setApplicationName(APP_ID)
    application.setApplicationDisplayName(APP_DISPLAY_NAME)
    application.setOrganizationName(APP_ID)
    application.setOrganizationDomain("pypackhub.local")
    application.setApplicationVersion(__version__)
    return application


def build_window(*, store: QSettingsStore | None = None) -> MainWindow:
    """Build the main window with the persisted settings already applied."""
    shell = WindowShell(store=store or QSettingsStore())
    shell.load()

    # ``shell=`` is PyPackHub's own window-shell object, not a command shell; this
    # is a bandit false positive on the keyword's name (B604, low confidence).
    window = MainWindow(shell=shell)  # nosec B604
    window.apply_theme()
    return window


def run(argv: list[str] | None = None) -> int:
    """Start the application and run Qt's event loop."""
    application = build_application(argv)
    register_bundled_fonts()
    settings_data_directory()

    window = build_window()
    window.show()

    return application.exec()
