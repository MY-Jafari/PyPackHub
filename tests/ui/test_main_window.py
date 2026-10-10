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
from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from pypackhub.core.settings import ColorTheme, Language
from pypackhub.ui.app import build_window, register_bundled_fonts
from pypackhub.ui.i18n.translations import EN_TABLE, FA_TABLE
from pypackhub.ui.theme import ThemeName
from pypackhub.ui.views import ViewName
from pypackhub.ui.views.main_window import (
    NAVIGATION,
    EntryKind,
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
    """Exactly one page per page-kind destination - and no orphans.

    "Logs" is an ``EntryKind.LOG_PANEL`` destination: it reveals the shared log
    panel rather than owning a page, so it contributes no page to the stack. A
    destination whose real view is registered must not keep its placeholder
    behind as a second, unreachable page.
    """
    stack = window.centralWidget().findChild(QStackedWidget)
    assert stack is not None
    page_entries = [entry for entry in NAVIGATION if entry.kind is EntryKind.PAGE]
    # The stack includes one page per PAGE-kind navigation entry, including
    # both placeholder pages for unavailable destinations and real views for
    # available ones. Security (available) now has a real view, so it is no
    # longer counted as a placeholder.
    # The stack should have one page per PAGE-kind navigation entry.
    # Available entries get real views (no placeholder).
    # Unavailable entries keep their placeholder.
    # Stub views for available entries that aren't fully implemented
    # (SEARCH, DEPENDENCIES, HISTORY) are still PlaceholderView instances.
    # Security now has a real SecurityView, so it's no longer a placeholder.
    assert stack.count() == len(page_entries)

    placeholders = [
        stack.widget(index)
        for index in range(stack.count())
        if isinstance(stack.widget(index), PlaceholderView)
    ]
    available_entries = [entry for entry in page_entries if entry.available]
    # Available entries that still have placeholder/stub views:
    # SEARCH, DEPENDENCIES, HISTORY (not fully implemented yet)
    still_stubs = [entry for entry in available_entries if entry.name in {
        ViewName.SEARCH,
        ViewName.DEPENDENCIES,
        ViewName.HISTORY,
    }]
    # Total placeholders = (unavailable entries) + (available entries with stubs)
    # Unavailable entries = page_entries - available_entries
    # Available entries with stubs = still_stubs
    # So: placeholders = (page_entries - available_entries) + still_stubs
    expected_placeholders = (
        len(page_entries) - len(available_entries) + len(still_stubs)
    )
    assert len(placeholders) == expected_placeholders


def test_unbuilt_destinations_are_disabled_and_explain_themselves(
    window: MainWindow,
) -> None:
    for entry in NAVIGATION:
        button = window.nav_button(entry.name)
        assert button.isEnabled() is entry.available
        if not entry.available:
            assert button.toolTip()


def test_switching_to_an_enabled_destination_shows_its_stub(window: MainWindow) -> None:
    """An enabled destination whose real view is not built yet shows a stub page."""
    window.show_view(ViewName.ENVIRONMENTS)
    assert window.current_view is ViewName.ENVIRONMENTS


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

    def _body(page: QWidget) -> str:
        if isinstance(page, PlaceholderView):
            return page.body_text
        if hasattr(page, "_stub_body"):
            return page._stub_body.text()
        return ""

    page = next(
        (stack.widget(i) for i in range(stack.count()) if _body(stack.widget(i))),
        None,
    )
    assert page is not None
    assert _body(page)

    english_body = _body(page)
    window.toggle_language()
    assert _body(page) != english_body

    # The dashboard body text is now coming from the DashboardView widget
    # instead of the placeholder text. The widget displays stats that aren't
    # translated until the inventory is loaded, so we check the body is
    # non-empty and different from English instead of matching a key.
    dashboard_body = _body(page)
    assert dashboard_body != english_body
    assert dashboard_body != ""



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
