"""History view for PyPackHub.

This module renders the history page showing a list of past package management
actions with their status, time, and details.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator


class HistoryEntry(Protocol):
    """A protocol for history entry data."""

    name: str
    version: str
    action: str
    timestamp: datetime
    status: str
    environment: str


@dataclass
class HistoryItem:
    """A history item for display."""

    name: str
    version: str
    action: str
    timestamp: datetime
    status: str
    environment: str

    def __hash__(self) -> int:
        return hash((self.name, self.version, self.action, self.timestamp))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HistoryItem):
            return False
        return (
            self.name == other.name
            and self.version == other.version
            and self.action == other.action
            and self.timestamp == other.timestamp
        )


class HistoryView(QWidget):
    """A history widget showing past package management actions."""

    # Emitted when user selects a history item
    selection_changed = Signal(object)

    def __init__(
        self,
        *,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._entries: list[HistoryItem] = []
        self._selected_item: HistoryItem | None = None

        self.setObjectName("historyPanel")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Header
        self._title_label = QLabel(self)
        self._title_label.setObjectName("pageTitle")
        self._title_label.setText(translator.translate(TranslationKey.HISTORY_TITLE))
        layout.addWidget(self._title_label)

        # Actions label
        self._actions_label = QLabel(self)
        self._actions_label.setObjectName("secondaryText")
        self._actions_label.setText(translator.translate(TranslationKey.HISTORY_ACTIONS))
        layout.addWidget(self._actions_label)

        # History list
        self._history_list = QListWidget(self)
        self._history_list.setObjectName("historyList")
        layout.addWidget(self._history_list)

        # Status for empty state
        self._empty_label = QLabel(self)
        self._empty_label.setObjectName("secondaryText")
        self._empty_label.setText(translator.translate(TranslationKey.HISTORY_NO_ACTIONS))
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

        # Connect selection changes
        self._history_list.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self) -> None:
        """Handle selection change in history list."""
        selected = self._history_list.selectedItems()
        if selected:
            item = selected[0]
            self._selected_item = item.data(Qt.ItemDataRole.UserRole)
            self.selection_changed.emit(self._selected_item)
        else:
            self._selected_item = None
            self.selection_changed.emit(None)

    def set_entries(self, entries: list[HistoryItem]) -> None:
        """Set the history entries to display."""
        self._history_list.clear()
        self._entries = entries

        if not entries:
            self._empty_label.setVisible(True)
            self._history_list.setVisible(False)
        else:
            self._empty_label.setVisible(False)
            self._history_list.setVisible(True)

            for entry in entries:
                item = QListWidgetItem()
                item.setText(f"{entry.action} {entry.name} ({entry.version})")
                item.setToolTip(
                    f"{entry.action} {entry.name} ({entry.version})\n"
                    f"Environment: {entry.environment}\n"
                    f"Time: {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Status: {entry.status}"
                )
                item.setData(Qt.ItemDataRole.UserRole, entry)
                self._history_list.addItem(item)

    def set_loading(self, loading: bool) -> None:
        """Set loading state."""
        if loading:
            self._history_list.clear()
            self._empty_label.setText(self._translator.translate(TranslationKey.HISTORY_NO_ACTIONS))
            self._empty_label.setVisible(True)

    def clear(self) -> None:
        """Clear all history entries."""
        self._history_list.clear()
        self._entries.clear()
        self._selected_item = None
        self._empty_label.setVisible(True)

    def retranslate(self, translator: Translator) -> None:
        """Re-render strings after language switch."""
        self._translator = translator
        self._title_label.setText(translator.translate(TranslationKey.HISTORY_TITLE))
        self._actions_label.setText(translator.translate(TranslationKey.HISTORY_ACTIONS))
        self._empty_label.setText(translator.translate(TranslationKey.HISTORY_NO_ACTIONS))

        # Update tooltips for existing items
        for i in range(self._history_list.count()):
            item = self._history_list.item(i)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry:
                item.setToolTip(
                    f"{entry.action} {entry.name} ({entry.version})\n"
                    f"Environment: {entry.environment}\n"
                    f"Time: {entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Status: {entry.status}"
                )

    def refresh(self) -> None:
        """Refresh the history view."""
        self.clear()
