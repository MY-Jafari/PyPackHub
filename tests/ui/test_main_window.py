"""The window itself: build it offscreen, switch theme and language, restart.

This is the section 3 gate from ``docs/DECISIONS.md``: an offscreen smoke test
where the theme and the language switch live. It runs with
``QT_QPA_PLATFORM=offscreen`` (set for the whole suite in ``tests/conftest.py``)
and skips itself when PySide6 is not installed, so the Qt-free security job in CI
still collects this file cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStackedWidget

from pypackhub.core.settings import ColorTheme, Language
from pypackhub.ui.app import build_window, register_bundled_fonts
from pypackhub.ui.i18n.translations import EN_TABLE, FA_TABLE
from pypackhub.ui.theme import ThemeName
from pypackhub.ui.views import ViewName
from pypackhub.ui.views.main_window import (
    NAVIGATION,
    MainWindow,
    PlaceholderView,
)
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell

pytestmark = pytest.mark.gui

#: Taken from the translation tables rather than typed here, so these tests fail
#: when a string changes and the window was not retranslated.
ENGLISH_TITLE = EN_TABLE["app.window_title"]
PERSIAN_TITLE = FA_TABLE["app.window_title"]
PERSIAN_PACKAGES = FA_TABLE["nav.packages"]


@pytest.fixture
def store(tmp_path: Path) -> QSettingsStore:
    return QSettingsStore(path=tmp_path / "settings.ini")


@pytest.fixture
def window(qapp: QApplication, store: QSettingsStore) -> MainWindow:
    shell = WindowShell(store=store)
    shell.load()
    built = MainWindow(shell=shell)
    built.apply_theme()
    return built


def _nav_labels(window: MainWindow) -> list[str]:
    return [window.nav_button(entry.name).text() for entry in NAVIGATION]


def test_window_builds_with_a_title_and_a_sidebar(window: MainWindow) -> None:
    assert window.windowTitle() == ENGLISH_TITLE
    assert window.sidebar is not None
    assert _nav_labels(window) == [
        "Dashboard",
        "Packages",
        "Environments",
        "Dependencies",
        "Search",
        "Security",
        "Snapshots",
        "History",
        "Logs",
        "Settings",
    ]


def test_the_content_stack_has_one_page_per_destination(window: MainWindow) -> None:
    stack = window.centralWidget().findChild(QStackedWidget)
    assert stack is not None
    assert stack.count() == len(NAVIGATION)


def test_unbuilt_destinations_are_disabled_and_explain_themselves(
    window: MainWindow,
) -> None:
    for entry in NAVIGATION:
        button = window.nav_button(entry.name)
        assert button.isEnabled() is entry.available
        if not entry.available:
            assert button.toolTip()


def test_switching_to_an_unbuilt_destination_changes_nothing(window: MainWindow) -> None:
    window.show_view(ViewName.SETTINGS)
    assert window.current_view is ViewName.DASHBOARD


def test_theme_switch_is_live_on_the_application(window: MainWindow, qapp: QApplication) -> None:
    before = qapp.styleSheet()
    window.toggle_theme()
    assert qapp.styleSheet() != before
    assert window.shell.settings.theme in {ColorTheme.LIGHT, ColorTheme.DARK}


def test_theme_switch_persists_across_a_restart(window: MainWindow, store: QSettingsStore) -> None:
    window.toggle_theme()
    theme = window.shell.settings.theme
    assert store.load()["theme"] == theme.value

    reopened = WindowShell(store=store)
    reopened.load()
    assert reopened.settings.theme is theme


def test_language_switch_flips_the_layout_direction(window: MainWindow, qapp: QApplication) -> None:
    window.toggle_language()
    assert window.shell.settings.language is Language.FA
    assert qapp.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert window.windowTitle() == PERSIAN_TITLE

    window.toggle_language()
    assert window.shell.settings.language is Language.EN
    assert qapp.layoutDirection() == Qt.LayoutDirection.LeftToRight
    assert window.windowTitle() == ENGLISH_TITLE


def test_the_stylesheet_is_mirrored_with_the_layout(window: MainWindow, qapp: QApplication) -> None:
    assert "border-right: 1px solid" in qapp.styleSheet()

    window.toggle_language()
    assert "border-left: 1px solid" in qapp.styleSheet()
    assert "border-right: 1px solid" not in qapp.styleSheet()


def test_language_switch_retranslates_the_navigation(window: MainWindow) -> None:
    window.toggle_language()
    assert PERSIAN_PACKAGES in _nav_labels(window)
    window.toggle_language()
    assert "Packages" in _nav_labels(window)


def test_language_switch_retranslates_the_placeholder_pages(window: MainWindow) -> None:
    stack = window.centralWidget().findChild(QStackedWidget)
    assert stack is not None
    page = stack.currentWidget()
    assert isinstance(page, PlaceholderView)

    english_body = page.body_text
    assert english_body

    window.toggle_language()
    assert page.body_text != english_body
    assert page.body_text == window.shell.text(page_body_key(window, ViewName.DASHBOARD))
    assert window.windowTitle() == PERSIAN_TITLE


def page_body_key(window: MainWindow, name: ViewName) -> str:
    """The translation key of a destination's placeholder body."""
    return next(entry.placeholder_key for entry in NAVIGATION if entry.name is name)


def test_sidebar_can_be_hidden_and_shown_again(window: MainWindow) -> None:
    assert window.sidebar.isVisible() or not window.isVisible()
    window.toggle_sidebar()
    assert not window.sidebar.isVisible()
    window.toggle_sidebar()
    assert window.sidebar.isVisibleTo(window)


def test_bundled_fonts_load(qapp: QApplication) -> None:
    families = register_bundled_fonts()
    assert "Inter" in families
    assert "Vazirmatn" in families


def test_closing_the_window_persists_its_geometry(
    window: MainWindow, store: QSettingsStore
) -> None:
    window.show()
    window.resize(1100, 700)
    window.close()

    stored = store.load()
    assert stored["main_window_geometry"]
    assert stored["main_window_state"]


def test_a_second_window_restores_the_stored_theme(store: QSettingsStore) -> None:
    first = build_window(store=store)
    first.toggle_theme()
    expected = first.shell.settings.theme

    second = build_window(store=store)
    assert second.shell.settings.theme is expected
    assert second.shell.active_theme.name in {ThemeName.LIGHT, ThemeName.DARK}
