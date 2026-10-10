"""Dashboard view for PyPackHub.

This module renders the dashboard page showing package counts, statistics,
and environment information from the inventory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.inventory import Inventory
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator


def _package_count(inventory: Inventory | None) -> int:
    """Return the total number of installed packages."""
    if inventory is None:
        return 0
    return len(inventory.rows)


def _outdated_count(inventory: Inventory | None) -> int:
    """Return the number of outdated packages."""
    if inventory is None:
        return 0
    return inventory.outdated_count


def _measured_size(inventory: Inventory | None) -> int:
    """Return measured size in bytes."""
    if inventory is None:
        return 0
    return inventory.total_size_bytes


def _today_packages(inventory: Inventory | None) -> int:
    """Return packages installed today."""
    if inventory is None:
        return 0
    today = datetime.now(timezone.utc).date()
    count = 0
    for row in inventory.rows:
        if hasattr(row, "installed_at") and row.installed_at is not None:
            try:
                installed_date = datetime.fromtimestamp(
                    row.installed_at, tz=timezone.utc
                ).date()
                if installed_date == today:
                    count += 1
            except (ValueError, OSError):
                pass
    return count


def _this_week_packages(inventory: Inventory | None) -> int:
    """Return packages installed this week."""
    if inventory is None:
        return 0
    today = datetime.now(timezone.utc).date()
    weekday = today.weekday()
    monday = today - timedelta(days=weekday)
    count = 0
    for row in inventory.rows:
        if hasattr(row, "installed_at") and row.installed_at is not None:
            try:
                installed_date = datetime.fromtimestamp(
                    row.installed_at, tz=timezone.utc
                ).date()
                if monday <= installed_date <= today:
                    count += 1
            except (ValueError, OSError):
                pass
    return count


class DashboardView(QWidget):
    """A dashboard widget showing package statistics.

    Displays:
    - Total packages installed
    - Outdated packages count
    - Packages installed today
    - Packages installed this week
    - Measured size of all packages
    """

    # Emitted when the dashboard needs to refresh its data
    refresh_requested = Signal()

    def __init__(
        self,
        *,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._inventory: Inventory | None = None
        self._loading = False
        self._error: str | None = None

        self.setObjectName("dashboardPanel")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        self._title_label = QLabel(self)
        self._title_label.setObjectName("pageTitle")
        self._title_label.setText(translator.translate(TranslationKey.DASHBOARD_TITLE))
        layout.addWidget(self._title_label)

        # Environment info
        self._env_label = QLabel(self)
        self._env_label.setObjectName("secondaryText")
        self._env_label.setWordWrap(True)
        layout.addWidget(self._env_label)

        layout.addSpacing(16)

        # Cards container
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(12)
        layout.addLayout(self._cards_layout)

        # Status
        self._status_label = QLabel(self)
        self._status_label.setObjectName("secondaryText")
        layout.addWidget(self._status_label)

        self._build_cards()

    def _build_cards(self) -> None:
        """Build the stat cards."""
        # Clear existing cards
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Total packages card
        self._total_packages_label = QLabel(self)
        self._total_packages_label.setObjectName("statValue")
        self._cards_layout.addWidget(self._total_packages_label)

        # Outdated packages card
        self._outdated_packages_label = QLabel(self)
        self._outdated_packages_label.setObjectName("statValue")
        self._cards_layout.addWidget(self._outdated_packages_label)

        # Today packages card
        self._today_packages_label = QLabel(self)
        self._today_packages_label.setObjectName("statValue")
        self._cards_layout.addWidget(self._today_packages_label)

        # This week packages card
        self._this_week_packages_label = QLabel(self)
        self._this_week_packages_label.setObjectName("statValue")
        self._cards_layout.addWidget(self._this_week_packages_label)

    def set_environment(self, env_path: str | None) -> None:
        """Set the current environment path for display."""
        if env_path:
            self._env_label.setText(env_path)
        else:
            self._env_label.setText(self._translator.translate(TranslationKey.DASHBOARD_ENVIRONMENT_UNKNOWN))

    def set_inventory(self, inventory: Inventory | None) -> None:
        """Set the inventory and update the dashboard."""
        self._inventory = inventory
        self._loading = False
        self._error = None
        self._update_display()

    def set_loading(self, loading: bool) -> None:
        """Set loading state."""
        self._loading = loading
        self._update_display()

    def set_error(self, error: str | None) -> None:
        """Set error state."""
        self._error = error
        self._loading = False
        self._update_display()

    def _update_display(self) -> None:
        """Update all labels based on current state."""
        translator = self._translator

        if self._error:
            self._status_label.setText(
                translator.translate(TranslationKey.DASHBOARD_UNABLE_TO_LOADING)
            )
            self._status_label.setStyleSheet("color: #ef4444;")
            return

        if self._loading:
            self._status_label.setText(
                translator.translate(TranslationKey.DASHBOARD_LOADING)
            )
            self._status_label.setStyleSheet("")
            return

        self._status_label.setText(
            translator.translate(TranslationKey.DASHBOARD_READY)
        )
        self._status_label.setStyleSheet("")

        # Update cards
        total = _package_count(self._inventory)
        outdated = _outdated_count(self._inventory)
        today = _today_packages(self._inventory)
        this_week = _this_week_packages(self._inventory)

        self._total_packages_label.setText(f"{total}")
        self._outdated_packages_label.setText(f"{outdated}")
        self._today_packages_label.setText(f"{today}")
        self._this_week_packages_label.setText(f"{this_week}")

    def retranslate(self, translator: Translator) -> None:
        """Re-render strings after language switch."""
        self._translator = translator
        self._title_label.setText(translator.translate(TranslationKey.DASHBOARD_TITLE))
        self._update_display()

    def refresh(self) -> None:
        """Request a refresh of the dashboard data."""
        self.refresh_requested.emit()
