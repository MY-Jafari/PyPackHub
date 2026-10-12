"""The snapshots view: record the installed state, and put it back.

A snapshot is written by the store in :mod:`pypackhub.core.snapshots` - a
small JSON file per capture, one directory per environment. This page is the
manual side of that: capture the current state, look at what exists, delete
what is no longer needed, and roll one back.

Creating and restoring never run a package manager here. Creating writes a
file. Restoring emits the snapshot's identity to the window, which hands it
to the packages view - where the preview shows every command the restore
will run (SPEC section 4: rollback with a preview diff), the automatic
snapshot before it is taken for free, and the confirmation is the one the
whole application already trusts.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.snapshots import Snapshot, SnapshotStore
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.spinner import Spinner, SpinnerSize
from pypackhub.ui.widgets.toast import ToastKind

__all__ = ["SnapshotItem", "SnapshotView"]


class SnapshotItem(QListWidgetItem):
    """One listed snapshot, carrying its record for the action buttons."""

    def __init__(self, snapshot: Snapshot, text: str) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.setText(text)


class SnapshotView(QWidget):
    """Create, inspect, delete and roll back snapshots of this environment."""

    #: (environment, snapshot id) for a restore the user asked for. The window
    #: routes it to the packages view, which previews and confirms it.
    rollback_requested = Signal(str, str)
    status_changed = Signal(str)
    toast = Signal(str, object)

    #: From worker threads; Qt queues them onto the GUI thread.
    created = Signal(object)
    listed = Signal(object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        store: SnapshotStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._store = store or SnapshotStore()
        self._task = ""

        self.setObjectName("snapshotsPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("pageTitle")
        layout.addWidget(self.title_label)

        self.intro_label = QLabel(self)
        self.intro_label.setObjectName("secondaryText")
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.create_button = QPushButton(self)
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self._create_clicked)
        controls.addWidget(self.create_button)

        self.refresh_button = QPushButton(self)
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)

        self.restore_button = QPushButton(self)
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self._restore_clicked)
        controls.addWidget(self.restore_button)

        self.delete_button = QPushButton(self)
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_clicked)
        controls.addWidget(self.delete_button)

        controls.addStretch(1)
        self.spinner = Spinner(size=SpinnerSize.SMALL)
        self.spinner.hide()
        controls.addWidget(self.spinner)
        layout.addLayout(controls)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("secondaryText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.list_label = QLabel(self)
        self.list_label.setObjectName("sectionHeading")
        layout.addWidget(self.list_label)

        self.snapshots_list = QListWidget(self)
        self.snapshots_list.setObjectName("snapshotsList")
        self.snapshots_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.snapshots_list, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        self.retranslate()
        self.created.connect(self._on_created)
        self.listed.connect(self._on_listed)

    # -- the store ------------------------------------------------------

    @property
    def environment(self) -> str:
        """The environment this page snapshots, as the store addresses it."""
        return str(self._context.default_environment)

    def refresh(self) -> None:
        """List the snapshots of the current environment on a worker thread."""
        store = self._store
        environment = self.environment

        def work(_task_id: str, _progress: object, _cancel: threading.Event) -> object:
            return store.list_snapshots(environment)

        self._context.tasks.schedule(
            self._shell.text(TranslationKey.SNAPSHOTS_REFRESH),
            work,
            on_finish=lambda _task_id, result: self.listed.emit(result),
        )

    def _on_listed(self, result: Any) -> None:
        snapshots = result.payload if isinstance(result.payload, list) else []
        self._render(snapshots)

    def _render(self, snapshots: list[Snapshot]) -> None:
        self.snapshots_list.clear()

        if not snapshots:
            self.empty_label.setText(self._shell.text(TranslationKey.SNAPSHOTS_NO_SNAPSHOTS))
            self.empty_label.setVisible(True)
            self.snapshots_list.setVisible(False)
        else:
            self.empty_label.setVisible(False)
            self.snapshots_list.setVisible(True)
            for snapshot in snapshots:
                self.snapshots_list.addItem(SnapshotItem(snapshot, self._row_text(snapshot)))

        self._on_selection_changed()

    def _row_text(self, snapshot: Snapshot) -> str:
        from datetime import datetime

        when = datetime.fromtimestamp(snapshot.created_at).strftime("%Y-%m-%d %H:%M:%S")
        kind = (
            TranslationKey.SNAPSHOTS_AUTOMATIC
            if snapshot.automatic
            else TranslationKey.SNAPSHOTS_MANUAL
        )
        return (
            f"{when} — {self._shell.text(kind)} — "
            f"{self._shell.text(TranslationKey.SNAPSHOTS_PACKAGES)}: {snapshot.package_count}"
        )

    # -- creating -------------------------------------------------------

    def _create_clicked(self) -> None:
        if self._task:
            return

        self._task = ""
        self.spinner.start()
        self.spinner.show()
        self.status_label.setText(self._shell.text(TranslationKey.SNAPSHOTS_CREATING))
        self.status_changed.emit(self._shell.text(TranslationKey.SNAPSHOTS_CREATING))

        context = self._context
        store = self._store
        environment = self._context.default_environment

        def work(_task_id: str, _progress: object, _cancel: threading.Event) -> object:
            service = context.service_for(environment)
            inventory = service.list()
            return store.capture(
                environment,
                [(row.name, row.installed) for row in inventory.rows],
                reason="manual",
            )

        def finished(_task_id: str, result: object) -> None:
            self._task = ""
            self.spinner.stop()
            self.spinner.hide()
            self.created.emit(result)

        self._task = context.tasks.schedule(
            self._shell.text(TranslationKey.SNAPSHOTS_CREATE),
            work,
            on_finish=finished,
        )

    def _on_created(self, result: Any) -> None:
        snapshot = result.payload if isinstance(result.payload, Snapshot) else None
        if snapshot is None:
            error = getattr(result, "error", "") or self._shell.text(TranslationKey.SNAPSHOTS_ERROR)
            message = f"{self._shell.text(TranslationKey.SNAPSHOTS_ERROR)}: {error}"
            self.status_label.setText(message)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return

        message = self._shell.text(TranslationKey.SNAPSHOTS_CREATED)
        self.status_label.setText(message)
        self.status_changed.emit(message)
        self.refresh()

    # -- the selected snapshot ------------------------------------------

    def _selected_snapshot(self) -> Snapshot | None:
        selected = self.snapshots_list.selectedItems()
        if not selected or not isinstance(selected[0], SnapshotItem):
            return None
        return selected[0].snapshot

    def _on_selection_changed(self) -> None:
        snapshot = self._selected_snapshot()
        self.restore_button.setEnabled(snapshot is not None)
        self.delete_button.setEnabled(snapshot is not None)

    def _restore_clicked(self) -> None:
        """Ask for a restore; the packages view previews and confirms it."""
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        self.rollback_requested.emit(self.environment, snapshot.id)

    def _delete_clicked(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return

        confirm = QMessageBox.question(
            self,
            self._shell.text(TranslationKey.SNAPSHOTS_TITLE),
            self._shell.text(TranslationKey.SNAPSHOTS_DELETE_CONFIRM),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self._store.delete(self.environment, snapshot.id):
            message = self._shell.text(TranslationKey.SNAPSHOTS_DELETED)
            self.status_label.setText(message)
            self.toast.emit(message, ToastKind.SUCCESS)
        self.refresh()

    # -- chrome --------------------------------------------------------

    def retranslate(self) -> None:
        shell = self._shell
        self.title_label.setText(shell.text(TranslationKey.SNAPSHOTS_TITLE))
        self.intro_label.setText(shell.text(TranslationKey.SNAPSHOTS_INTRO))
        self.create_button.setText(shell.text(TranslationKey.SNAPSHOTS_CREATE))
        self.refresh_button.setText(shell.text(TranslationKey.SNAPSHOTS_REFRESH))
        self.restore_button.setText(shell.text(TranslationKey.SNAPSHOTS_RESTORE))
        self.delete_button.setText(shell.text(TranslationKey.SNAPSHOTS_DELETE))
        self.list_label.setText(shell.text(TranslationKey.SNAPSHOTS_LIST))
        self.empty_label.setText(shell.text(TranslationKey.SNAPSHOTS_NO_SNAPSHOTS))

        # Rows carry their own translated words; re-render them in place.
        for row in range(self.snapshots_list.count()):
            item = self.snapshots_list.item(row)
            if isinstance(item, SnapshotItem):
                item.setText(self._row_text(item.snapshot))
