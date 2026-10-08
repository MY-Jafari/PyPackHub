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

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
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
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub import __version__
from pypackhub.core.settings import AppSettings, ColorTheme, Language
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.views import ViewName
from pypackhub.ui.views.window_shell import WindowShell

__all__ = [
    "NAVIGATION",
    "MainWindow",
    "NavigationEntry",
    "PlaceholderView",
]


@dataclass(frozen=True, slots=True)
class NavigationEntry:
    """One sidebar destination."""

    name: ViewName
    label_key: str
    placeholder_key: str
    #: False until the phase that builds the view lands. The entry stays visible
    #: but disabled, so the user can see where the feature will live.
    available: bool = False


#: The sidebar, in the order SPEC section 7 lists the destinations. Every entry
#: is visible from section 3 on; the ones whose views arrive later are disabled
#: and their placeholder page names the phase that builds them.
NAVIGATION: tuple[NavigationEntry, ...] = (
    NavigationEntry(
        ViewName.DASHBOARD,
        TranslationKey.NAV_DASHBOARD,
        TranslationKey.VIEW_PLACEHOLDER_DASHBOARD,
    ),
    NavigationEntry(
        ViewName.PACKAGES,
        TranslationKey.NAV_PACKAGES,
        TranslationKey.VIEW_PLACEHOLDER_PACKAGES,
    ),
    NavigationEntry(
        ViewName.ENVIRONMENTS,
        TranslationKey.NAV_ENVIRONMENTS,
        TranslationKey.VIEW_PLACEHOLDER_ENVIRONMENTS,
    ),
    NavigationEntry(
        ViewName.DEPENDENCIES,
        TranslationKey.NAV_DEPENDENCIES,
        TranslationKey.VIEW_PLACEHOLDER_DEPENDENCIES,
    ),
    NavigationEntry(
        ViewName.SEARCH,
        TranslationKey.NAV_SEARCH,
        TranslationKey.VIEW_PLACEHOLDER_SEARCH,
    ),
    NavigationEntry(
        ViewName.SECURITY,
        TranslationKey.NAV_SECURITY,
        TranslationKey.VIEW_PLACEHOLDER_SECURITY,
    ),
    NavigationEntry(
        ViewName.SNAPSHOTS,
        TranslationKey.NAV_SNAPSHOTS,
        TranslationKey.VIEW_PLACEHOLDER_SNAPSHOTS,
    ),
    NavigationEntry(
        ViewName.HISTORY,
        TranslationKey.NAV_HISTORY,
        TranslationKey.VIEW_PLACEHOLDER_HISTORY,
    ),
    NavigationEntry(
        ViewName.LOGS,
        TranslationKey.NAV_LOGS,
        TranslationKey.VIEW_PLACEHOLDER_LOGS,
    ),
    NavigationEntry(
        ViewName.SETTINGS,
        TranslationKey.NAV_SETTINGS,
        TranslationKey.VIEW_PLACEHOLDER_SETTINGS,
    ),
)


class PlaceholderView(QWidget):
    """A page shown for a destination whose real view is not built yet.

    It is an honest empty state, not a pretend screen: it names the phase that
    will build the view and says what will appear there.
    """

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

    def __init__(self, *, shell: WindowShell | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell or WindowShell()
        self._views: dict[ViewName, QWidget] = {}
        self._nav_buttons: dict[ViewName, QPushButton] = {}
        self._current_view = ViewName.DASHBOARD

        self.setObjectName("mainWindow")
        self.setMinimumSize(960, 600)
        self.resize(1280, 800)

        self._build_ui()
        self._build_menus()
        self.retranslate()
        self._restore_window_state()

    # -- public API ------------------------------------------------------------

    @property
    def shell(self) -> WindowShell:
        return self._shell

    @property
    def sidebar(self) -> QWidget:
        """The collapsible navigation panel."""
        return self._sidebar

    @property
    def current_view(self) -> ViewName:
        return self._current_view

    def nav_button(self, name: ViewName) -> QPushButton:
        """The sidebar button for a destination."""
        return self._nav_buttons[name]

    def register_view(self, name: ViewName, view: QWidget) -> None:
        """Plug a real view into the shell, replacing its placeholder page.

        Section 4 and later phases call this. The navigation entry switches to
        enabled and the stack shows the widget; nothing else in the window has to
        change.
        """
        self._views[name] = view
        self._stack.addWidget(view)
        button = self._nav_buttons.get(name)
        if button is not None:
            button.setEnabled(True)
            button.setToolTip("")

    def show_view(self, name: ViewName) -> None:
        """Switch to a destination.

        A destination whose view is not built yet stays put: the button is
        disabled and this is a no-op, so the stack never shows a page the user
        cannot navigate away from.
        """
        button = self._nav_buttons.get(name)
        if button is not None and not button.isEnabled():
            return

        page = self._views.get(name)
        if page is None:
            page = self._placeholder_for(name)
        self._current_view = name
        self._stack.setCurrentWidget(page)

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
        for entry in NAVIGATION:
            self._stack.addWidget(self._placeholder_for(entry.name))
        body_layout.addWidget(self._stack, 1)

        root_layout.addWidget(body, 1)
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
        # Section 2's discovery results are wired in when the package view lands;
        # until then the switcher states the truth: nothing has been scanned.
        self._environment_switcher.addItem("")
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

    # -- rendering -------------------------------------------------------------

    def apply_theme(self) -> None:
        """Push the active theme at the whole application, live."""
        application = QApplication.instance()
        target = application if application is not None else self
        self._shell.apply_to(target)

    def retranslate(self) -> None:
        """Re-render every string in the current language."""
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
            if isinstance(page, PlaceholderView):
                page.retranslate(shell)

        self._view_menu.setTitle(shell.text(TranslationKey.MENU_VIEW))
        self._theme_action.setText(shell.text(TranslationKey.SETTINGS_THEME))
        self._language_action.setText(shell.text(TranslationKey.SETTINGS_LANGUAGE))
        self._sidebar_action.setText(shell.text(TranslationKey.MENU_VIEW_SIDEBAR))
        self._environment_label.setText(shell.text(TranslationKey.TOPBAR_ENVIRONMENT))
        self._search_input.setPlaceholderText(shell.text(TranslationKey.TOPBAR_SEARCH_PLACEHOLDER))
        self._environment_switcher.setItemText(0, shell.text(TranslationKey.TOPBAR_NO_ENVIRONMENT))
        self._theme_button.setToolTip(shell.text(TranslationKey.TOPBAR_TOGGLE_THEME))
        self._language_button.setText(
            "EN" if shell.translator.resolved_language is Language.EN else "\u0641\u0627"
        )
        self._language_button.setToolTip(shell.text(TranslationKey.TOPBAR_TOGGLE_LANGUAGE))
        self.statusBar().showMessage(shell.text(TranslationKey.APP_VERSION, version=__version__))

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
        """Persist the window geometry, then let Qt close the window."""
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
