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
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QStackedWidget, QWidget

import pypackhub.ui.views.packages as packages_module
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
from pypackhub.ui.widgets.log_panel import RunOutcome

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
    monkeypatch.setattr(
        "pypackhub.ui.views.environments.detected_environments", lambda roots, registered=(): None
    )
    # The top-bar switcher's discovery goes through the provider, which also
    # walks the machine's real home on a worker thread - one that can
    # outlive the window that scheduled it, because the walk takes seconds
    # on a real disk. Sixteen windows meant sixteen real scans racing their
    # own teardowns, which is where the suite's intermittent hard crashes
    # came from. Pinning the provider's scan keeps these tests about the
    # window, not about the disk they happen to run on; the switcher's own
    # test pins the context itself and is unaffected.
    monkeypatch.setattr(
        "pypackhub.ui.services.detected_environments",
        lambda roots, registered=(): type("Listing", (), {"environments": ()})(),
    )
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


def test_the_settings_install_request_runs_from_the_settings_page(
    window: MainWindow, monkeypatch
) -> None:
    """Picking a missing manager in Settings and answering "install now"
    must land in the one confirm-and-run path - the same log, history and
    confirmation every other change goes through - not in a private
    shortcut around it.

    The user stays on the page they asked from: the manager row owns its
    own small progress bar now, so the run is watched where it started
    rather than somewhere it takes a page switch to reach.

    The signal's slot is bound at construction, so this drives the real
    ``install_manager`` with its service and queue replaced at the seams
    the method looks up: the batch is recorded, nothing real is installed.
    """
    from pypackhub.core.backends.base import CommandPlan

    recorded: list[tuple[tuple[CommandPlan, bool], ...]] = []

    class Accepting:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    service = window._packages._context.service_for(window._packages.environment)
    monkeypatch.setattr(window._packages, "refresh", lambda: None)
    monkeypatch.setattr(
        service,
        "bootstrap_plans",
        lambda name, action: [
            CommandPlan(
                argv=("python", "-m", "pip", "install", "--upgrade", "--", name),
                action=action,
                label=f"install {name}",
                backend="pip",
            )
        ],
    )
    monkeypatch.setattr(service, "bootstrap_needs_elevation", lambda: False)
    monkeypatch.setattr(packages_module, "ConfirmationDialog", Accepting)
    monkeypatch.setattr(
        window._packages,
        "run_confirmed_plans",
        lambda plans: recorded.append(tuple(plans)),
    )

    # The user is on the settings page when they pick the manager - that is
    # where the question was asked and answered.
    window.show_view(ViewName.SETTINGS)
    window._settings_view.manager_install_requested.emit("pipx")

    # The user stays on the page they asked from: the row's own bar shows
    # the run, and the window-wide log opens below whichever page is
    # showing. The nav says so too - Settings is still the checked page.
    assert window.current_view is ViewName.SETTINGS
    assert recorded and len(recorded[0]) == 1
    assert recorded[0][0][0].label == "install pipx"
    assert window._nav_buttons[ViewName.SETTINGS].isChecked()
    assert not window._nav_buttons[ViewName.PACKAGES].isChecked()


def test_the_settings_remove_request_runs_from_the_settings_page(
    window: MainWindow, monkeypatch
) -> None:
    """Removal runs the same way: from the settings page it was asked on,
    through the one confirm-and-run path, with the exact command recorded."""
    from pypackhub.core.backends.base import CommandPlan

    recorded: list[tuple[tuple[CommandPlan, bool], ...]] = []

    class Accepting:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    service = window._packages._context.service_for(window._packages.environment)
    monkeypatch.setattr(window._packages, "refresh", lambda: None)
    monkeypatch.setattr(
        service,
        "manager_removal_plan",
        lambda name, action: CommandPlan(
            argv=("python", "-m", "pip", "uninstall", "--yes", "--", name),
            action=action,
            label=f"uninstall {name}",
            backend="pip",
        ),
    )
    monkeypatch.setattr(service, "bootstrap_needs_elevation", lambda: False)
    monkeypatch.setattr(packages_module, "ConfirmationDialog", Accepting)
    monkeypatch.setattr(
        window._packages,
        "run_confirmed_plans",
        lambda plans: recorded.append(tuple(plans)),
    )

    window.show_view(ViewName.SETTINGS)
    window._settings_view.manager_remove_requested.emit("pipx")

    assert window.current_view is ViewName.SETTINGS
    assert recorded and len(recorded[0]) == 1
    assert recorded[0][0][0].label == "uninstall pipx"
    assert window._nav_buttons[ViewName.SETTINGS].isChecked()
    assert not window._nav_buttons[ViewName.PACKAGES].isChecked()


def test_the_manager_row_bar_is_wired_to_the_one_run_path(window: MainWindow) -> None:
    """The row's bar is fed by the run, not by the settings page's own
    guesses: the packages view's signals - started, percentage, outcome -
    land in the settings page the window connects them to, so a run
    started from the row is watched on the row, through the run's own
    numbers and its own ending."""
    bar = window._settings_view._manager_progress
    assert bar.isHidden()

    window._packages.manager_run_started.emit(True)
    assert not bar.isHidden()
    assert bar.maximum() == 100

    window._packages.manager_progress.emit(61)
    assert bar.value() == 61

    window._packages.manager_run_finished.emit(RunOutcome.FAILED)
    assert not bar.isHidden()
    assert bar.property("error") is True


def test_a_run_reveals_the_log_panel(window: MainWindow) -> None:
    """The report: a manager install ran with the log hidden, so there was
    nothing on screen saying it was happening. The log starts hidden as a
    layout choice for an empty window, not a promise that runs happen in
    the dark - the moment one starts, the window opens it."""
    assert window._log_panel.isHidden()

    window._packages.run_started.emit()

    assert not window._log_panel.isHidden()


def test_switching_pages_leaves_exactly_one_checked_nav_button(
    window: MainWindow,
) -> None:
    """The report: every page ever visited kept its checked state, so the
    sidebar read as if several pages were open at once. The buttons are
    checkable - a click checks its own - and the window owns unchecking
    the page the user just left."""
    window.show_view(ViewName.HISTORY)
    assert [name for name, button in window._nav_buttons.items() if button.isChecked()] == [
        ViewName.HISTORY
    ]

    window.show_view(ViewName.PACKAGES)
    assert [name for name, button in window._nav_buttons.items() if button.isChecked()] == [
        ViewName.PACKAGES
    ]


def test_the_log_entry_reports_the_panel_not_the_page(window: MainWindow) -> None:
    """The log entry is a toggle for a panel, not a page: its checked state
    follows the panel's visibility across page switches, so the button
    never claims the log is closed while it is showing - including the
    moment a run opens the log on its own."""
    window.show_view(ViewName.PACKAGES)
    assert not window._nav_buttons[ViewName.LOGS].isChecked()

    window._packages.run_started.emit()

    assert window._nav_buttons[ViewName.LOGS].isChecked()
    window.show_view(ViewName.HISTORY)
    assert window._nav_buttons[ViewName.LOGS].isChecked()

    window.set_log_panel_visible(False)
    assert not window._nav_buttons[ViewName.LOGS].isChecked()
