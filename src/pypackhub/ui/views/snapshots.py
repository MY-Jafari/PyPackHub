"""Snapshots view for PyPackHub.

This module renders the snapshots page showing environment snapshots
with create/restore/delete functionality.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator


@dataclass
class Snapshot:
    """A snapshot of an environment."""

    name: str
    prefix: str
    environment: str
    package_count: int
    created_at: datetime
    id: str


class SnapshotView(QWidget):
    """A snapshots widget for managing environment snapshots."""

    # Emitted when user wants to create/restore/delete a snapshot
    create_requested = Signal()
    restore_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(
        self,
        *,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._snapshots: list[Snapshot] = []
        self._selected_snapshot: Snapshot | None = None
        self._creating = False

        self.setObjectName("snapshotsPanel")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Header
        self._title_label = QLabel(self)
        self._title_label.setObjectName("pageTitle")
        self._title_label.setText(translator.translate(TranslationKey.SNAPSHOTS_TITLE))
        layout.addWidget(self._title_label)

        # Create button
        self._create_button = QPushButton(self)
        self._create_button.setObjectName("primaryButton")
        self._create_button.setText(translator.translate(TranslationKey.SNAPSHOTS_CREATE))
        self._create_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._create_button.clicked.connect(self._on_create_clicked)
        layout.addWidget(self._create_button)

        # Status label
        self._status_label = QLabel(self)
        self._status_label.setObjectName("secondaryText")
        layout.addWidget(self._status_label)

        # Snapshots list
        self._list_label = QLabel(self)
        self._list_label.setObjectName("secondaryText")
        self._list_label.setText(translator.translate(TranslationKey.SNAPSHOTS_LIST))
        layout.addWidget(self._list_label)

        self._snapshots_list = QListWidget(self)
        self._snapshots_list.setObjectName("snapshotsList")
        layout.addWidget(self._snapshots_list)

        # Empty state
        self._empty_label = QLabel(self)
        self._empty_label.setObjectName("secondaryText")
        self._empty_label.setText(translator.translate(TranslationKey.SNAPSHOTS_NO_SNAPSHOTS))
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

        # Connect selection changes
        self._snapshots_list.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self) -> None:
        """Handle selection change in snapshots list."""
        selected = self._snapshots_list.selectedItems()
        if selected:
            item = selected[0]
            self._selected_snapshot = item.data(Qt.ItemDataRole.UserRole)
            # Enable restore/delete buttons if we add them later
        else:
            self._selected_snapshot = None

    def _on_create_clicked(self) -> None:
        """Handle create button click."""
        if self._creating:
            return
        self._creating = True
        self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATING))
        self._status_label.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATING))
        self.create_requested.emit()

    def set_creating(self, creating: bool) -> None:
        """Set creating state."""
        self._creating = creating
        if creating:
            self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATING))
            self._status_label.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATING))
        else:
            self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATE))
            self._status_label.clear()

    def set_created(self) -> None:
        """Set created state."""
        self._creating = False
        self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATE))
        self._status_label.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATED))

    def set_error(self, error: str) -> None:
        """Set error state."""
        self._creating = False
        self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATE))
        self._status_label.setText(
                    f"{self._translator.translate(TranslationKey.SNAPSHOTS_ERROR)}: {error}"
                )

    def set_snapshots(self, snapshots: list[Snapshot]) -> None:
        """Set the snapshots to display."""
        self._snapshots = snapshots
        self._snapshots_list.clear()

        if not snapshots:
            self._empty_label.setVisible(True)
            self._snapshots_list.setVisible(False)
        else:
            self._empty_label.setVisible(False)
            self._snapshots_list.setVisible(True)

            for snapshot in snapshots:
                item = QListWidgetItem()
                item.setText(f"{snapshot.name} ({snapshot.created_at.strftime('%Y-%m-%d %H:%M')})")
                item.setToolTip(
                    f"Name: {snapshot.name}\n"
                    f"Environment: {snapshot.environment}\n"
                    f"Prefix: {snapshot.prefix}\n"
                    f"Packages: {snapshot.package_count}\n"
                    f"Created: {snapshot.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                item.setData(Qt.ItemDataRole.UserRole, snapshot)
                self._snapshots_list.addItem(item)

    def retranslate(self, translator: Translator) -> None:
        """Re-render strings after language switch."""
        self._translator = translator
        self._title_label.setText(translator.translate(TranslationKey.SNAPSHOTS_TITLE))
        self._create_button.setText(translator.translate(TranslationKey.SNAPSHOTS_CREATE))
        self._list_label.setText(translator.translate(TranslationKey.SNAPSHOTS_LIST))
        self._empty_label.setText(translator.translate(TranslationKey.SNAPSHOTS_NO_SNAPSHOTS))

        # Update tooltips for existing items
        for i in range(self._snapshots_list.count()):
            item = self._snapshots_list.item(i)
            snapshot = item.data(Qt.ItemDataRole.UserRole)
            if snapshot:
                item.setToolTip(
                    f"Name: {snapshot.name}\n"
                    f"Environment: {snapshot.environment}\n"
                    f"Prefix: {snapshot.prefix}\n"
                    f"Packages: {snapshot.package_count}\n"
                    f"Created: {snapshot.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                item.setText(f"{snapshot.name} ({snapshot.created_at.strftime('%Y-%m-%d %H:%M')})")

    def refresh(self) -> None:
        """Refresh the snapshots view."""
        self._snapshots_list.clear()
        self._snapshots.clear()
        self._selected_snapshot = None
        self._creating = False
        self._create_button.setText(self._translator.translate(TranslationKey.SNAPSHOTS_CREATE))
        self._status_label.clear()
        self._empty_label.setVisible(True)
        self._empty_label.setText(self._translator.translate(TranslationKey.SNAPSHOTS_NO_SNAPSHOTS))
