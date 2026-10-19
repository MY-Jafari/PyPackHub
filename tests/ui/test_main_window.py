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
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget

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
from pypackhub.ui.views.settings import PACKAGE_MANAGER_CHOICES
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
def window(qapp, store, monkeypatch) -> MainWindow:
    # The environments page now discovers on a worker thread; these tests
    # are about the window, so the scan is pinned to an empty answer rather
    # than walking the machine's real home directory fifteen times.
    monkeypatch.setattr("pypackhub.ui.views.environments.detected_environments", lambda roots: None)
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
    assert stack.count() == len(page_entries)

    pages = [stack.widget(index) for index in range(stack.count())]
    # Every destination has its real view now, so no placeholder is left behind,
    # and no destination is registered twice under two pages: a replaced page that
    # stays in the stack is a page the user can never reach again.
    assert not [page for page in pages if isinstance(page, PlaceholderView)]
    assert len(set(pages)) == len(pages)


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


def test_the_language_button_names_the_language_it_switches_to(
    window: MainWindow,
) -> None:
    """The button reads as the *other* language, so it stays legible.

    In Persian it says "en" (click for English); in English it says "Fa"
    (click for Persian). Showing the active language's own script instead
    made the affordance unreadable in exactly the direction it mattered.
    """
    assert window.shell.translator.resolved_language is Language.EN
    assert window._language_button.text() == "Fa"

    window.toggle_language()
    assert window.shell.settings.language is Language.FA
    assert window._language_button.text() == "en"

    window.toggle_language()
    assert window._language_button.text() == "Fa"


def test_language_switch_retranslates_the_navigation(window: MainWindow) -> None:
    window.toggle_language()
    assert PERSIAN_PACKAGES in _nav_labels(window)
    window.toggle_language()
    assert "Packages" in _nav_labels(window)


def _visible_labels(page: QWidget) -> list[str]:
    """Every non-empty label text on one page, in child order."""
    return [label.text() for label in page.findChildren(QLabel) if label.text()]


def test_language_switch_re_renders_every_registered_page(window: MainWindow) -> None:
    """Every page follows the language, not only the sidebar and the placeholders.

    The pass used to name the views that existed when it was written, so Search,
    History, Security, Snapshots, Environments and the dashboard kept the previous
    language while the navigation switched around them. A half-translated window
    is worse than one that does not translate at all, which is why this checks
    every registered page rather than one of them.
    """
    stack = window.centralWidget().findChild(QStackedWidget)
    assert stack is not None
    pages = [stack.widget(index) for index in range(stack.count())]

    window.retranslate()
    before = [(page, _visible_labels(page)) for page in pages]
    assert all(texts for _, texts in before), "a page with no text proves nothing"

    window.toggle_language()

    stale = [type(page).__name__ for page, texts in before if _visible_labels(page) == texts]
    assert stale == []
    assert window.windowTitle() == PERSIAN_TITLE


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


def test_picking_a_package_manager_applies_to_the_running_window(window: MainWindow) -> None:
    """The settings page's choice reaches the context the views share, mid-session.

    The page used to ask the shell for a context it never had, so the change was
    silently skipped and only a restart honoured the new manager. The window now
    hands the page its own context, which is what this holds in place.
    """
    assert window.context.package_manager == "auto"

    window._settings_view._package_manager.setCurrentIndex(PACKAGE_MANAGER_CHOICES.index("uv"))

    assert window.shell.settings.package_manager == "uv"
    assert window.context.package_manager == "uv"


def test_environment_discovery_fills_the_switcher_off_the_gui_thread(
    qtbot, window: MainWindow, monkeypatch
) -> None:
    """Discovery runs on a worker thread; the combo fills when it answers.

    The window used to scan the filesystem inside showEvent's timer - still
    the GUI thread, freezing the frame the moment the window appeared on a
    machine with a slow or networked home directory.
    """
    where = Path("/fake/env").resolve()
    monkeypatch.setattr(window.context, "environments", lambda: (where,))

    window.load_environments()
    # Waiting on the row itself, not the count: the combo carries a
    # placeholder row from construction, so any count is already non-zero
    # before discovery answers.
    qtbot.waitUntil(lambda: window._environment_switcher.findData(str(where)) >= 0, timeout=5000)

    assert window._environment_switcher.currentData() == str(where)
