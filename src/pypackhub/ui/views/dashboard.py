"""The dashboard: the environment at a glance.

Six answers, all of them real data (SPEC section 2): how many packages are
installed, how many are behind the index, how many nothing needs, how many
the last security scan found affected, how much measured space they take
up, and what the environment is probably *for* - inferred from the packages
it carries.

Where each answer comes from matters as much as the answer:

* the counts and the size come from the packages view's inventory, which is
  already loaded and enriched when this page is refreshed;
* the orphan count is computed on a worker thread from the same metadata
  the dependency graph uses;
* the vulnerable count is the last security scan's, shown with its time so
  a stale number can be recognised as one;
* the purpose is a local, deterministic guess - the same environment never
  describes itself differently on two refreshes.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.deps import build_graph
from pypackhub.core.environments import infer_purpose
from pypackhub.core.inventory import Inventory
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.worker_signals import emit_if_alive

__all__ = ["DashboardView"]

#: One translation key per purpose the inference can name.
_PURPOSE_KEYS = {
    "data-science": TranslationKey.PURPOSE_DATA_SCIENCE,
    "machine-learning": TranslationKey.PURPOSE_MACHINE_LEARNING,
    "web-api": TranslationKey.PURPOSE_WEB_API,
    "scraping-automation": TranslationKey.PURPOSE_SCRAPING_AUTOMATION,
    "gui-desktop": TranslationKey.PURPOSE_GUI_DESKTOP,
    "dev-tooling": TranslationKey.PURPOSE_DEV_TOOLING,
    "general": TranslationKey.PURPOSE_GENERAL,
}

#: (heading key, value label object name) pairs, in display order. The purpose
#: is a sentence rather than a number, so it uses the same style as the rest
#: of the page's secondary text.
_CARDS: tuple[tuple[TranslationKey, str], ...] = (
    (TranslationKey.DASHBOARD_TOTAL_PACKAGES, "statValue"),
    (TranslationKey.DASHBOARD_OUTDATED_PACKAGES, "statValue"),
    (TranslationKey.DASHBOARD_ORPHANS, "statValue"),
    (TranslationKey.DASHBOARD_VULNERABLE, "statValue"),
    (TranslationKey.DASHBOARD_SIZE, "statValue"),
    (TranslationKey.DASHBOARD_PURPOSE, "secondaryText"),
)


class DashboardView(QWidget):
    """The environment's numbers, each with the source it came from."""

    refresh_requested = Signal()
    status_changed = Signal(str)
    toast = Signal(str, object)

    #: From a worker thread; Qt queues it onto the GUI thread.
    orphans_ready = Signal(object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._inventory: Inventory | None = None
        self._vulnerable_count: int | None = None
        self._vulnerable_time: str = ""
        self._orphan_count: int | None = None
        self._orphans_task: str = ""
        self._purpose: str = "general"

        self.setObjectName("dashboardPanel")
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("pageTitle")
        layout.addWidget(self.title_label)

        self.environment_label = QLabel(self)
        self.environment_label.setObjectName("secondaryText")
        self.environment_label.setWordWrap(True)
        layout.addWidget(self.environment_label)

        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(12)
        layout.addLayout(self.cards_layout)

        self._build_cards()

        self.status_label = QLabel(self)
        self.status_label.setObjectName("secondaryText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.refresh_button = QPushButton(self)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(self.refresh_button)

        self.retranslate()
        self.orphans_ready.connect(self._on_orphans_ready)

    def _build_cards(self) -> None:
        self._headings: dict[TranslationKey, QLabel] = {}
        self._values: dict[TranslationKey, QLabel] = {}

        for key, value_style in _CARDS:
            heading = QLabel(self)
            heading.setObjectName("secondaryText")
            self._headings[key] = heading
            self.cards_layout.addWidget(heading)

            value = QLabel(self)
            value.setObjectName(value_style)
            self._values[key] = value
            self.cards_layout.addWidget(value)

    # -- data -----------------------------------------------------------

    def set_data(
        self,
        inventory: Inventory | None,
        *,
        vulnerable_count: int | None = None,
        vulnerable_time: str = "",
    ) -> None:
        """Adopt the packages view's inventory and the last scan's count."""
        self._inventory = inventory
        self._vulnerable_count = vulnerable_count
        self._vulnerable_time = vulnerable_time
        self._render()

    def refresh(self) -> None:
        """Render the data the window handed over, and count orphans.

        Deliberately does *not* emit ``refresh_requested`` - the button does
        that - so the window can hand data over and re-render the page
        without talking to itself.
        """
        self._render()
        self._count_orphans()

    def _count_orphans(self) -> None:
        """Count orphans on a worker thread, from the shared metadata index."""
        # One count at a time: every visit here schedules a listing subprocess,
        # so rapid view-switching would otherwise stack them.
        if self._orphans_task:
            return

        context = self._context
        environment = context.default_environment

        def work(_task_id: str, _progress: object, _cancel: threading.Event) -> object:
            service = context.service_for(environment)
            index = service.metadata_index()
            inventory = service.list()
            graph = build_graph(index, installed_names=tuple(row.name for row in inventory.rows))
            return graph.orphans()

        self._orphans_task = context.tasks.schedule(
            self._shell.text(TranslationKey.DASHBOARD_TITLE),
            work,
            on_finish=lambda _task_id, result: emit_if_alive(self.orphans_ready, result),
        )

    def _on_orphans_ready(self, result: Any) -> None:
        self._orphans_task = ""
        report = result.payload
        orphans = getattr(report, "orphans", None)
        if orphans is not None:
            self._orphan_count = len(orphans)
            self._render()

    # -- rendering ------------------------------------------------------

    def _render(self) -> None:
        self.environment_label.setText(str(self._context.default_environment))

        inventory = self._inventory
        rows = inventory.rows if inventory is not None else ()

        def value(key: TranslationKey) -> QLabel:
            return self._values[key]

        value(TranslationKey.DASHBOARD_TOTAL_PACKAGES).setText(str(len(rows)))
        value(TranslationKey.DASHBOARD_OUTDATED_PACKAGES).setText(
            str(inventory.outdated_count) if inventory is not None else "—"
        )
        value(TranslationKey.DASHBOARD_ORPHANS).setText(
            str(self._orphan_count) if self._orphan_count is not None else "…"
        )
        value(TranslationKey.DASHBOARD_VULNERABLE).setText(
            str(self._vulnerable_count) if self._vulnerable_count is not None else "—"
        )
        if inventory is not None and inventory.measured_size_count:
            value(TranslationKey.DASHBOARD_SIZE).setText(format_bytes(inventory.total_size_bytes))
        else:
            value(TranslationKey.DASHBOARD_SIZE).setText("—")

        self._purpose = infer_purpose(row.name for row in rows) if rows else "general"
        value(TranslationKey.DASHBOARD_PURPOSE).setText(self._purpose_text())

        if self._vulnerable_time:
            self.status_label.setText(
                self._shell.text(TranslationKey.SECURITY_LAST_SCAN, time=self._vulnerable_time)
            )
        else:
            self.status_label.clear()

    def _purpose_text(self) -> str:
        return self._shell.text(_PURPOSE_KEYS.get(self._purpose, TranslationKey.PURPOSE_GENERAL))

    # -- chrome --------------------------------------------------------

    def retranslate(self) -> None:
        shell = self._shell
        self.title_label.setText(shell.text(TranslationKey.DASHBOARD_TITLE))
        self.refresh_button.setText(shell.text(TranslationKey.SNAPSHOTS_REFRESH))
        for key, heading in self._headings.items():
            heading.setText(shell.text(key))
        self._render()
