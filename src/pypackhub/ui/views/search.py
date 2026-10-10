"""Search view for PyPackHub.

This module renders the search page showing PyPI search results with
package names, versions, summaries, and install buttons.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.inventory import PackageRow
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator


class SearchResultItem(QListWidgetItem):
    """A search result item in the list."""

    def __init__(self, name: str, version: str, summary: str, installed: bool) -> None:
        super().__init__()
        self.name = name
        self.version = version
        self.summary = summary
        self.installed = installed
        self.setText(f"{name} ({version})")


class SearchView(QWidget):
    """A search widget for finding packages on PyPI.

    Displays:
    - Search input field
    - Results list with package info
    - Install buttons for each result
    """

    # Emitted when user wants to install a package
    install_requested = Signal(str)

    def __init__(
        self,
        *,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._results: list[SearchResultItem] = []
        self._installed_packages: set[str] = set()
        self._loading = False
        self._error: str | None = None

        self.setObjectName("searchPanel")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Header
        self._title_label = QLabel(self)
        self._title_label.setObjectName("pageTitle")
        self._title_label.setText(translator.translate(TranslationKey.SEARCH_TITLE))
        layout.addWidget(self._title_label)

        # Search input
        self._search_edit = QPushButton(self)
        self._search_edit.setObjectName("searchInput")
        self._search_edit.setText(translator.translate(TranslationKey.SEARCH_QUERY))
        self._search_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_edit.clicked.connect(self._on_search_clicked)
        layout.addWidget(self._search_edit)

        # Results label
        self._results_label = QLabel(self)
        self._results_label.setObjectName("secondaryText")
        self._results_label.setText(translator.translate(TranslationKey.SEARCH_RESULTS))
        layout.addWidget(self._results_label)

        # Results list
        self._results_list = QListWidget(self)
        self._results_list.setObjectName("searchResultsList")
        layout.addWidget(self._results_list)

        # Status
        self._status_label = QLabel(self)
        self._status_label.setObjectName("secondaryText")
        layout.addWidget(self._status_label)

    def _on_search_clicked(self) -> None:
        """Handle search button click."""
        # TODO: Implement actual PyPI search
        self._status_label.setText(self._translator.translate(TranslationKey.SEARCH_LOADING))
        # For now, show a message
        self._status_label.setText("Search functionality will be implemented in a future update.")

    def set_installed_packages(self, packages: list[PackageRow]) -> None:
        """Set the list of installed packages to check against."""
        self._installed_packages = {pkg.name for pkg in packages}

    def set_results(self, results: list[tuple[str, str, str, bool]]) -> None:
        """Set search results: list of (name, version, summary, installed)."""
        self._results_list.clear()
        self._results.clear()
        for name, version, summary, installed in results:
            item = SearchResultItem(name, version, summary, installed)
            self._results_list.addItem(item)
            self._results.append(item)

    def set_loading(self, loading: bool) -> None:
        """Set loading state."""
        self._loading = loading
        if loading:
            self._status_label.setText(self._translator.translate(TranslationKey.SEARCH_LOADING))
        else:
            self._status_label.clear()

    def set_error(self, error: str | None) -> None:
        """Set error state."""
        self._error = error
        if error:
            self._status_label.setText(
                f"{self._translator.translate(TranslationKey.SEARCH_ERROR)}: {error}"
            )

    def retranslate(self, translator: Translator) -> None:
        """Re-render strings after language switch."""
        self._translator = translator
        self._title_label.setText(translator.translate(TranslationKey.SEARCH_TITLE))
        self._search_edit.setText(translator.translate(TranslationKey.SEARCH_QUERY))
        self._results_label.setText(translator.translate(TranslationKey.SEARCH_RESULTS))
        self._status_label.setText("")

    def refresh(self) -> None:
        """Refresh the search view."""
        self._results_list.clear()
        self._results.clear()
        self._status_label.clear()
