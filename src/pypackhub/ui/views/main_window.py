"""The PyPackHub main window.

This is the shell the rest of the application plugs into. It draws the layout
SPEC section 7 asks for:

* a collapsible left sidebar with the destinations the application knows about;
* a top bar with the environment switcher, the global search box and the theme /
  language toggles;
* the central content area, currently a stack of placeholder pages;
* a status bar.

The window owns no data of its own. It asks
:class:`pypackhub.ui.views.window_shell.WindowShell` what to render, tells it when
the user changes the theme or the language, and lets it persist the change.

Views that do not exist yet land on a translated placeholder page and their
navigation entry is disabled with a tooltip saying which phase builds them. When
section 4 (the package view) and later phases arrive, they register their widget
through :meth:`MainWindow.register_view` and the placeholder is replaced - the
navigation entries never have to change.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub import __version__
from pypackhub.core.settings import AppSettings, ColorTheme, Language
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.theme.design_tokens import DesignTokens
from pypackhub.ui.views import ViewName
from pypackhub.ui.views.dashboard import DashboardView
from pypackhub.ui.views.dependencies import DependenciesView
from pypackhub.ui.views.environments import EnvironmentsView
from pypackhub.ui.views.history import HistoryView
from pypackhub.ui.views.packages import PackagesView
from pypackhub.ui.views.search import SearchView
from pypackhub.ui.views.security import SecurityView
from pypackhub.ui.views.settings import SettingsView
from pypackhub.ui.views.snapshots import SnapshotView
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.log_panel import LogPanel
from pypackhub.ui.widgets.toast import ToastHost, ToastKind

if TYPE_CHECKING:
    pass

__all__ = [
    "NAVIGATION",
    "EntryKind",
    "MainWindow",
    "NavigationEntry",
    "PlaceholderView",
]


class EntryKind(str, Enum):
    """What clicking a sidebar entry does."""

    PAGE = "page"
    LOG_PANEL = "log_panel"


@dataclass(frozen=True, slots=True)
class NavigationEntry:
    """One sidebar destination."""

    name: ViewName
    label_key: str
    placeholder_key: str
    available: bool = False
    kind: EntryKind = EntryKind.PAGE


NAVIGATION: tuple[NavigationEntry, ...] = (
    NavigationEntry(
        ViewName.DASHBOARD,
        TranslationKey.NAV_DASHBOARD,
        TranslationKey.VIEW_PLACEHOLDER_DASHBOARD,
        available=True,
    ),
    NavigationEntry(
        ViewName.PACKAGES,
        TranslationKey.NAV_PACKAGES,
        TranslationKey.VIEW_PLACEHOLDER_PACKAGES,
        available=True,
    ),
    NavigationEntry(
        ViewName.ENVIRONMENTS,
        TranslationKey.NAV_ENVIRONMENTS,
        TranslationKey.VIEW_PLACEHOLDER_ENVIRONMENTS,
        available=True,
    ),
    NavigationEntry(
        ViewName.DEPENDENCIES,
        TranslationKey.NAV_DEPENDENCIES,
        TranslationKey.VIEW_PLACEHOLDER_DEPENDENCIES,
        available=True,
    ),
    NavigationEntry(
        ViewName.SEARCH,
        TranslationKey.NAV_SEARCH,
        TranslationKey.VIEW_PLACEHOLDER_SEARCH,
        available=True,
    ),
    NavigationEntry(
        ViewName.SECURITY,
        TranslationKey.NAV_SECURITY,
        TranslationKey.VIEW_PLACEHOLDER_SECURITY,
        available=True,
    ),
    NavigationEntry(
        ViewName.SNAPSHOTS,
        TranslationKey.NAV_SNAPSHOTS,
        TranslationKey.VIEW_PLACEHOLDER_SNAPSHOTS,
        available=True,
    ),
    NavigationEntry(
        ViewName.HISTORY,
        TranslationKey.NAV_HISTORY,
        TranslationKey.VIEW_PLACEHOLDER_HISTORY,
        available=True,
    ),
    NavigationEntry(
        ViewName.LOGS,
        TranslationKey.NAV_LOGS,
        TranslationKey.VIEW_PLACEHOLDER_LOGS,
        available=True,
        kind=EntryKind.LOG_PANEL,
    ),
    NavigationEntry(
        ViewName.SETTINGS,
        TranslationKey.NAV_SETTINGS,
        TranslationKey.VIEW_PLACEHOLDER_SETTINGS,
        available=True,
    ),
)


#: Pages whose ``retranslate`` re-renders from the shell or the context they were
#: built with, and therefore takes no argument. Every other page takes the
#: translator. Kept as one list rather than guessed at the call site, because
#: calling a no-argument ``retranslate`` with an argument raises inside the
#: language switch.
SHELL_AWARE_PAGES: Final = (
    PackagesView,
    SettingsView,
    DependenciesView,
    EnvironmentsView,
    SearchView,
    SecurityView,
    SnapshotView,
    HistoryView,
    DashboardView,
)


class PlaceholderView(QWidget):
    """A page shown for a destination whose real view is not built yet."""

    def __init__(self, *, title_key: str, body_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title_key = title_key
        self._body_key = body_key

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(8)
        layout.addStretch(1)

        self._title = QLabel(self)
        self._title.setObjectName("emptyStateTitle")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)

        self._body = QLabel(self)
        self._body.setObjectName("emptyStateBody")
        self._body.setWordWrap(True)
        layout.addWidget(self._body)
        layout.addStretch(2)

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def body_text(self) -> str:
        return self._body.text()

    def retranslate(self, shell: WindowShell) -> None:
        self._title.setText(shell.text(self._title_key))
        self._body.setText(shell.text(self._body_key))


class MainWindow(QMainWindow):
    """The application window."""

    def __init__(
        self,
        *,
        shell: WindowShell | None = None,
        context: UiContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shell = shell or WindowShell()
        self._context = context or UiContext()
        self._views: dict[ViewName, QWidget] = {}
        self._nav_buttons: dict[ViewName, QPushButton] = {}
        self._current_view = ViewName.DASHBOARD
        self._environments_loaded = False

        self.setObjectName("mainWindow")
        self.setMinimumSize(960, 600)
        self.resize(1280, 800)

        self._build_ui()
        self._build_menus()
        self._install_views()
        self._toasts = ToastHost(self)
        self.retranslate()
        self._restore_window_state()

    # -- public API ------------------------------------------------------------

    @property
    def shell(self) -> WindowShell:
        return self._shell

    @property
    def sidebar(self) -> QWidget:
        return self._sidebar

    @property
    def current_view(self) -> ViewName:
        return self._current_view

    def nav_button(self, name: ViewName) -> QPushButton:
        return self._nav_buttons[name]

    @property
    def context(self) -> UiContext:
        return self._context

    @property
    def packages_view(self) -> PackagesView:
        return self._packages

    @property
    def log_panel(self) -> LogPanel:
        return self._log_panel

    @property
    def toasts(self) -> ToastHost:
        return self._toasts

    def register_view(self, name: ViewName, view: QWidget) -> None:
        """Put *view* in the stack for *name*, replacing whatever was there.

        Both the placeholder and any earlier registration are removed: a page that
        was replaced must not stay in the stack as a second, unreachable copy of
        the same destination.
        """
        placeholder = self._placeholders.pop(name, None)
        if placeholder is not None:
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()

        previous = self._views.get(name)
        if previous is not None and previous is not view:
            self._stack.removeWidget(previous)
            previous.deleteLater()

        self._views[name] = view
        self._stack.addWidget(view)
        button = self._nav_buttons.get(name)
        if button is not None:
            button.setEnabled(True)
            button.setToolTip("")

    def _install_views(self) -> None:
        self._packages = PackagesView(
            context=self._context,
            shell=self._shell,  # nosec B604
            log_panel=self._log_panel,
            parent=self,
        )
        self.register_view(ViewName.PACKAGES, self._packages)

        self._settings_view = SettingsView(
            shell=self._shell,  # nosec B604
            context=self._context,
            parent=self,
        )
        self._settings_view.load_settings(self._shell.settings)
        self.register_view(ViewName.SETTINGS, self._settings_view)

        self._dependencies_view = DependenciesView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.DEPENDENCIES, self._dependencies_view)
        self._dependencies_view.status_changed.connect(self._on_status_changed)
        self._dependencies_view.removal_requested.connect(self._packages.confirm_and_run)

        self._packages.status_changed.connect(self._on_status_changed)
        self._packages.toast.connect(self._show_toast)
        self._log_panel.cancel_requested.connect(self._packages.cancel_running)
        self._log_panel.hide_requested.connect(lambda: self.set_log_panel_visible(False))
        self._search_input.textChanged.connect(self._on_search_changed)

        self._search_view = SearchView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.SEARCH, self._search_view)
        self._search_view.install_requested.connect(self._packages.confirm_and_run)
        self._search_view.status_changed.connect(self._on_status_changed)
        self._search_view.toast.connect(self._show_toast)

        self._history_view = HistoryView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.HISTORY, self._history_view)
        self._history_view.undo_requested.connect(self._packages.undo_action)
        self._history_view.status_changed.connect(self._on_status_changed)
        self._history_view.toast.connect(self._show_toast)

        self._security_view = SecurityView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.SECURITY, self._security_view)
        self._security_view.fix_requested.connect(self._packages.confirm_and_run)
        self._security_view.status_changed.connect(self._on_status_changed)
        self._security_view.toast.connect(self._show_toast)

        self._snapshots_view = SnapshotView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.SNAPSHOTS, self._snapshots_view)
        self._snapshots_view.rollback_requested.connect(
            lambda environment, snapshot_id: self._packages.restore_snapshot(
                environment, snapshot_id
            )
        )
        self._snapshots_view.status_changed.connect(self._on_status_changed)
        self._snapshots_view.toast.connect(self._show_toast)

        self._environments_view = EnvironmentsView(
            context=self._context,
            shell=self._shell,
            parent=self,
        )
        self.register_view(ViewName.ENVIRONMENTS, self._environments_view)
        self._environments_view.environment_selected.connect(self._on_environment_selected)
        self._environments_view.toast.connect(self._show_toast)

        self._dashboard_view = DashboardView(
            context=self._context,
            shell=self._shell,  # nosec B604
            parent=self,
        )
        self.register_view(ViewName.DASHBOARD, self._dashboard_view)
        self._dashboard_view.refresh_requested.connect(self._refresh_dashboard)
        self._dashboard_view.status_changed.connect(self._on_status_changed)
        self._dashboard_view.toast.connect(self._show_toast)

    def show_view(self, name: ViewName) -> None:
        """Show a view by name."""
        button = self._nav_buttons.get(name)
        if button is not None and not button.isEnabled():
            return

        entry = _entry_for(name)
        if entry is not None and entry.kind is EntryKind.LOG_PANEL:
            self.set_log_panel_visible(not self._log_visible)
            return

        page = self._views.get(name)
        if page is None:
            page = self._placeholders.get(name)
        if page is None:
            return
        self._current_view = name
        self._stack.setCurrentWidget(page)

        if name is ViewName.DEPENDENCIES:
            self._dependencies_view.refresh()

        if name is ViewName.PACKAGES and hasattr(self, "_packages"):
            packages = self._packages
            if packages.inventory is None and not packages.is_busy:
                packages.refresh()

        if name is ViewName.DASHBOARD and hasattr(self, "_dashboard_view"):
            self._refresh_dashboard()

        if name is ViewName.HISTORY and hasattr(self, "_history_view"):
            self._history_view.refresh()

        if name is ViewName.SNAPSHOTS and hasattr(self, "_snapshots_view"):
            self._snapshots_view.refresh()

        for view_name, nav_button in self._nav_buttons.items():
            nav_button.setChecked(view_name is name)

    # -- building --------------------------------------------------------------

    def _build_ui(self) -> None:

        root = QWidget(self)
        root.setObjectName("contentRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_top_bar())

        body = QWidget(root)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._sidebar = self._build_sidebar()
        body_layout.addWidget(self._sidebar)

        self._stack = QStackedWidget(body)
        self._placeholders: dict[ViewName, QWidget] = {}
        for entry in NAVIGATION:
            if entry.kind is EntryKind.PAGE:
                placeholder = self._placeholder_for(entry.name)
                self._placeholders[entry.name] = placeholder
                self._stack.addWidget(placeholder)
        body_layout.addWidget(self._stack, 1)

        self._log_panel = LogPanel(translator=self._shell.translator, parent=root)
        self._log_panel.setVisible(False)

        self._log_visible = False
        self._body_splitter: QSplitter | None = None

        self._content_splitter = QSplitter(Qt.Orientation.Vertical, root)
        self._body_splitter = QSplitter(Qt.Orientation.Vertical, root)
        self._body_splitter.addWidget(body)
        self._body_splitter.addWidget(self._log_panel)
        self._body_splitter.setStretchFactor(0, 4)
        self._body_splitter.setStretchFactor(1, 1)
        self._body_splitter.setSizes([body.height(), 0])
        self._body_splitter.setCollapsible(1, False)
        self._content_splitter.addWidget(self._body_splitter)
        self._content_splitter.setStretchFactor(0, 1)
        root_layout.addWidget(self._content_splitter, 1)

        self.setCentralWidget(root)

        self.setStatusBar(QStatusBar(self))
        self.show_view(ViewName.DASHBOARD)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(self._shell.settings.sidebar_width)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        self._sidebar_title = QLabel(sidebar)
        self._sidebar_title.setObjectName("sidebarTitle")
        layout.addWidget(self._sidebar_title)

        self._sidebar_subtitle = QLabel(sidebar)
        self._sidebar_subtitle.setObjectName("sidebarSubtitle")
        layout.addWidget(self._sidebar_subtitle)
        layout.addSpacing(8)

        for entry in NAVIGATION:
            button = QPushButton(sidebar)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(entry.available)
            if not entry.available:
                button.setToolTip(entry.placeholder_key)
            button.clicked.connect(lambda _checked=False, name=entry.name: self.show_view(name))
            layout.addWidget(button)
            self._nav_buttons[entry.name] = button

        layout.addStretch(1)

        self._safety_notice = QLabel(sidebar)
        self._safety_notice.setObjectName("safetyNotice")
        self._safety_notice.setWordWrap(True)
        layout.addWidget(self._safety_notice)

        return sidebar

    def _build_top_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self._sidebar_toggle = QToolButton(bar)
        self._sidebar_toggle.setObjectName("ghostButton")
        self._sidebar_toggle.setText("\u2630")
        self._sidebar_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_toggle.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self._sidebar_toggle)

        self._environment_label = QLabel(bar)
        self._environment_label.setObjectName("environmentLabel")
        layout.addWidget(self._environment_label)

        self._environment_switcher = QComboBox(bar)
        self._environment_switcher.setObjectName("environmentSwitcher")
        self._environment_switcher.setMinimumWidth(240)
        self._environment_switcher.addItem("")
        self._environment_switcher.currentIndexChanged.connect(self._on_environment_changed)
        layout.addWidget(self._environment_switcher)

        layout.addStretch(1)

        self._search_input = QLineEdit(bar)
        self._search_input.setObjectName("searchInput")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(260)
        self._search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._search_input)

        self._theme_button = QToolButton(bar)
        self._theme_button.setObjectName("ghostButton")
        self._theme_button.setText("\u25d0")
        self._theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self._theme_button)

        self._language_button = QToolButton(bar)
        self._language_button.setObjectName("ghostButton")
        self._language_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_button.clicked.connect(self.toggle_language)
        layout.addWidget(self._language_button)

        return bar

    def _build_menus(self) -> None:
        view_menu = self.menuBar().addMenu("")

        self._theme_action = QAction(self)
        self._theme_action.setShortcut(QKeySequence("Ctrl+T"))
        self._theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self._theme_action)

        self._language_action = QAction(self)
        self._language_action.setShortcut(QKeySequence("Ctrl+L"))
        self._language_action.triggered.connect(self.toggle_language)
        view_menu.addAction(self._language_action)

        self._sidebar_action = QAction(self)
        self._sidebar_action.setShortcut(QKeySequence("Ctrl+B"))
        self._sidebar_action.triggered.connect(self.toggle_sidebar)
        view_menu.addAction(self._sidebar_action)

        self._log_action = QAction(self)
        self._log_action.setShortcut(QKeySequence("Ctrl+J"))
        self._log_action.triggered.connect(self.toggle_log_panel)
        view_menu.addAction(self._log_action)

        self._view_menu = view_menu

    def _placeholder_for(self, name: ViewName) -> QWidget:
        entry = next(item for item in NAVIGATION if item.name is name)
        return PlaceholderView(
            title_key=TranslationKey.VIEW_EMPTY_TITLE,
            body_key=entry.placeholder_key,
            parent=self,
        )

    # -- user actions ----------------------------------------------------------

    def toggle_sidebar(self) -> None:
        self._sidebar.setVisible(not self._sidebar.isVisible())

    def toggle_theme(self) -> None:
        self._shell.toggle_theme()
        self.apply_theme()
        self._shell.save()
        self.statusBar().showMessage(
            self._shell.text(TranslationKey.SETTINGS_THEME) + ": " + self._theme_label()
        )

    def toggle_language(self) -> None:
        self._shell.toggle_language()
        self.apply_theme()
        self.retranslate()
        self._shell.save()

    def toggle_log_panel(self) -> None:
        self.set_log_panel_visible(not self._log_visible)

    def set_log_panel_visible(self, visible: bool) -> None:
        # Only the log panel is shown or hidden. Hiding the splitter that holds
        # both the panel and the body would hide the entire window content.
        self._log_visible = visible
        self._log_panel.setVisible(visible)
        self._update_log_splitter(visible)
        button = self._nav_buttons.get(ViewName.LOGS)
        if button is not None:
            button.setChecked(visible)

    # -- environment -----------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._environments_loaded:
            self._environments_loaded = True
            QTimer.singleShot(0, self.load_environments)

    def load_environments(self) -> None:
        environments = self._context.environments()
        self._environment_switcher.blockSignals(True)
        self._environment_switcher.clear()
        for prefix in environments:
            self._environment_switcher.addItem(str(prefix), str(prefix))
        self._environment_switcher.blockSignals(False)

        if self._environment_switcher.count() == 0:
            self._environment_switcher.addItem(
                self._shell.text(TranslationKey.TOPBAR_NO_ENVIRONMENT), ""
            )
            return

        current = self._context.default_environment
        index = next(
            (
                position
                for position in range(self._environment_switcher.count())
                if self._environment_switcher.itemData(position) == str(current)
            ),
            0,
        )
        self._environment_switcher.setCurrentIndex(index)
        self._on_environment_changed(index)

    def _on_environment_changed(self, index: int) -> None:
        if index < 0:
            return
        value = self._environment_switcher.itemData(index)
        if not value:
            return
        self._packages.set_environment(Path(str(value)))

    def _on_environment_selected(self, prefix: str) -> None:
        """Adopt an environment the user picked on the Environments page.

        The page is a richer switcher than the top-bar combo, so switching there
        has to move the packages view and the combo with it; otherwise the window
        would show two different "current" environments.
        """
        self._packages.set_environment(Path(prefix))
        index = self._environment_switcher.findData(prefix)
        if index >= 0:
            self._environment_switcher.blockSignals(True)
            self._environment_switcher.setCurrentIndex(index)
            self._environment_switcher.blockSignals(False)

    # -- application signals ---------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._packages.search_edit.setText(text)
        if text.strip() and self._current_view is not ViewName.PACKAGES:
            self.show_view(ViewName.PACKAGES)

    def _on_status_changed(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _refresh_dashboard(self) -> None:
        """Refresh the dashboard data from the current inventory and scan."""
        packages = self._packages
        if packages.inventory is None:
            packages.refresh()

        security = self._security_view
        vulnerable_time = ""
        report = security.report
        if report is not None and report.timestamp:
            from datetime import datetime

            vulnerable_time = datetime.fromtimestamp(report.timestamp).strftime("%H:%M:%S")

        self._dashboard_view.set_data(
            packages.inventory,
            vulnerable_count=security.vulnerable_count,
            vulnerable_time=vulnerable_time,
        )
        self._dashboard_view.refresh()

    def _show_toast(self, text: str, kind: object) -> None:
        toast_kind = kind if isinstance(kind, ToastKind) else ToastKind.INFO
        self._toasts.show_message(text, toast_kind)

    def _update_log_splitter(self, visible: bool) -> None:
        if not self._body_splitter_visible():
            return
        split = self._body_splitter
        available = split.height() if split is not None else 0
        log_height = int(available * 0.32) if visible else 0
        body_height = max(0, available - log_height)
        if split is not None:
            split.setSizes([body_height, log_height])

    def _body_splitter_visible(self) -> bool:
        return bool(self._body_splitter and self._body_splitter.isVisible())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self._body_splitter_visible():
            self._update_log_splitter(self._log_visible)

    # -- rendering -------------------------------------------------------------

    def apply_theme(self) -> None:
        application = QApplication.instance()
        target = application if application is not None else self
        theme = self._shell.apply_to(target)
        self._adopt_tokens(theme.tokens)

    def _adopt_tokens(self, tokens: DesignTokens) -> None:
        self._packages.apply_theme(tokens)
        self._toasts.apply_tokens(tokens)

    def retranslate(self) -> None:
        shell = self._shell
        self.setWindowTitle(shell.text(TranslationKey.APP_WINDOW_TITLE))
        self._sidebar_title.setText(shell.text(TranslationKey.APP_TITLE))
        self._sidebar_subtitle.setText(shell.text(TranslationKey.APP_SUBTITLE))
        self._safety_notice.setText(shell.text(TranslationKey.SAFETY_NOTICE))

        for entry in NAVIGATION:
            button = self._nav_buttons[entry.name]
            button.setText(shell.text(entry.label_key))
            if not entry.available:
                button.setToolTip(shell.text(entry.placeholder_key))

        for index in range(self._stack.count()):
            page = self._stack.widget(index)
            if page is not None:
                self._retranslate_page(page)

        self._view_menu.setTitle(shell.text(TranslationKey.MENU_VIEW))
        self._theme_action.setText(shell.text(TranslationKey.SETTINGS_THEME))
        self._language_action.setText(shell.text(TranslationKey.SETTINGS_LANGUAGE))
        self._sidebar_action.setText(shell.text(TranslationKey.MENU_VIEW_SIDEBAR))
        self._log_action.setText(shell.text(TranslationKey.NAV_LOGS))
        self._environment_label.setText(shell.text(TranslationKey.TOPBAR_ENVIRONMENT))
        self._search_input.setPlaceholderText(shell.text(TranslationKey.TOPBAR_SEARCH_PLACEHOLDER))
        self._environment_switcher.setItemText(0, shell.text(TranslationKey.TOPBAR_NO_ENVIRONMENT))
        self._theme_button.setToolTip(shell.text(TranslationKey.TOPBAR_TOGGLE_THEME))
        self._language_button.setText(
            "EN" if shell.translator.resolved_language is not Language.FA else "\u0641\u0627"
        )
        self._language_button.setToolTip(shell.text(TranslationKey.TOPBAR_TOGGLE_LANGUAGE))
        # The stack loop above covers every registered page; the log panel is not
        # a page, so it is re-rendered here.
        self._log_panel.retranslate(shell.translator)
        self.statusBar().showMessage(shell.text(TranslationKey.APP_VERSION, version=__version__))

    def _retranslate_page(self, page: QWidget) -> None:
        """Re-render one registered page in the language the shell now speaks.

        The pass used to name the two views that happened to exist when it was
        written, so every page registered since then kept the previous language:
        the sidebar said one thing and the Search, History, Security and Snapshots
        pages said another. A page is asked to re-render if it can, whatever it is.
        """
        if isinstance(page, PlaceholderView):
            page.retranslate(self._shell)
            return

        retranslate = getattr(page, "retranslate", None)
        if not callable(retranslate):
            return

        if isinstance(page, SHELL_AWARE_PAGES):
            retranslate()
        else:
            retranslate(self._shell.translator)

    def _theme_label(self) -> str:
        theme = self._shell.settings.theme
        if theme is ColorTheme.DARK:
            return self._shell.text(TranslationKey.THEME_DARK)
        if theme is ColorTheme.LIGHT:
            return self._shell.text(TranslationKey.THEME_LIGHT)
        return self._shell.text(TranslationKey.THEME_SYSTEM)

    # -- window state ----------------------------------------------------------

    def _restore_window_state(self) -> None:
        settings = self._shell.settings
        geometry = _decode(settings.main_window_geometry)
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = _decode(settings.main_window_state)
        if state is not None:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._shell.apply_settings(
            _with_window_state(
                self._shell.settings,
                geometry=_encode(self.saveGeometry()),
                state=_encode(self.saveState()),
            )
        )
        self._shell.save()
        super().closeEvent(event)


def _encode(value: QByteArray) -> str:
    """Base64 a Qt byte array so it survives an INI file intact."""
    return base64.b64encode(value.data()).decode("ascii")


def _decode(value: str) -> QByteArray | None:
    if not value:
        return None
    try:
        return QByteArray(base64.b64decode(value, validate=True))
    except (ValueError, TypeError):
        return None


def _with_window_state(settings: AppSettings, *, geometry: str, state: str) -> AppSettings:
    """Return a copy of *settings* with the window geometry replaced."""
    return replace(settings, main_window_geometry=geometry, main_window_state=state)


def _entry_for(name: ViewName) -> NavigationEntry | None:
    """The navigation entry for a destination, if it has one."""
    for entry in NAVIGATION:
        if entry.name is name:
            return entry
    return None
