"""The history view: what ran, when, and - when possible - take it back.

The task manager records every changing action the packages view runs: the
command, the outcome, the moment it finished, and the context the view
attached - the action's kind, the packages it touched, and the automatic
snapshot taken before it. This page lists those records; selecting one shows
its exact command, and offers the undo when there is one to offer.

Undo is not a re-run of the reverse command: it is the snapshot the action
was preceded by, narrowed to the action's own packages, applied through the
same preview-and-confirm path as every other bulk change. An uninstall is
therefore as reversible as an upgrade - the versions to put back were
recorded before anything was removed.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.runner import CommandResult
from pypackhub.core.tasks import JobState, TaskResult
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.toast import ToastKind

__all__ = ["HistoryEntryItem", "HistoryView"]

#: The states a finished action can be shown in, with their badge words.
_STATE_KEYS = {
    JobState.FINISHED: TranslationKey.HISTORY_SUCCESS,
    JobState.FAILED: TranslationKey.HISTORY_FAILED,
    JobState.CANCELLED: TranslationKey.HISTORY_CANCELLED,
}


class HistoryEntryItem(QListWidgetItem):
    """One recorded action, carrying its task result and undo context."""

    def __init__(self, result: TaskResult, text: str) -> None:
        super().__init__()
        self.result = result
        self.setText(text)


class HistoryView(QWidget):
    """The changing actions that ran, newest first, with undo where it exists."""

    #: The task context of the selected action (the ``meta`` the packages
    #: view recorded). The window routes it to the packages view, which
    #: previews and confirms the restore.
    undo_requested = Signal(object)
    status_changed = Signal(str)
    toast = Signal(str, object)

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

        self.setObjectName("historyPanel")

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
        self.refresh_button = QPushButton(self)
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)

        self.undo_button = QPushButton(self)
        self.undo_button.setObjectName("primaryButton")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._undo_selected)
        controls.addWidget(self.undo_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.history_list = QListWidget(self)
        self.history_list.setObjectName("historyList")
        self.history_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.history_list, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)

        self.retranslate()

    # -- listing --------------------------------------------------------

    def refresh(self) -> None:
        """Re-render the recorded actions from the task manager's history."""
        entries = [
            result
            for result in reversed(self._context.tasks.history())
            if isinstance(result.payload, CommandResult)
        ]

        self.history_list.clear()

        if not entries:
            self.empty_label.setText(self._shell.text(TranslationKey.HISTORY_NO_ACTIONS))
            self.empty_label.setVisible(True)
            self.history_list.setVisible(False)
        else:
            self.empty_label.setVisible(False)
            self.history_list.setVisible(True)
            for result in entries:
                self.history_list.addItem(HistoryEntryItem(result, self._row_text(result)))

        self._on_selection_changed()

    def _row_text(self, result: TaskResult) -> str:
        when = (
            datetime.fromtimestamp(result.timestamp).strftime("%H:%M:%S")
            if result.timestamp
            else ""
        )
        state_key = _STATE_KEYS.get(result.state, TranslationKey.HISTORY_STATUS)
        outcome = self._shell.text(state_key)
        names = result.meta.get("names") or []
        targets = ", ".join(str(name) for name in names)
        kind = str(result.meta.get("kind", "")).strip()
        label = f"{kind}: {targets}" if kind else result.error or outcome
        return f"{when} — {label} — {outcome}"

    # -- the selected action -------------------------------------------

    def _selected_result(self) -> TaskResult | None:
        selected = self.history_list.selectedItems()
        if not selected or not isinstance(selected[0], HistoryEntryItem):
            return None
        return selected[0].result

    def _on_selection_changed(self) -> None:
        result = self._selected_result()
        undoable = result is not None and bool(result.meta.get("snapshot"))
        self.undo_button.setEnabled(undoable)

    def _undo_selected(self) -> None:
        """Ask for the restore; the packages view previews and confirms it."""
        result = self._selected_result()
        if result is None:
            return

        if not result.meta.get("snapshot"):
            message = self._shell.text(TranslationKey.HISTORY_UNDO_UNAVAILABLE)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.INFO)
            return

        self.undo_requested.emit(result.meta)

    # -- chrome --------------------------------------------------------

    def retranslate(self) -> None:
        shell = self._shell
        self.title_label.setText(shell.text(TranslationKey.HISTORY_TITLE))
        self.intro_label.setText(shell.text(TranslationKey.HISTORY_INTRO))
        self.refresh_button.setText(shell.text(TranslationKey.SNAPSHOTS_REFRESH))
        self.undo_button.setText(shell.text(TranslationKey.HISTORY_UNDO))
        self.empty_label.setText(shell.text(TranslationKey.HISTORY_NO_ACTIONS))

        for row in range(self.history_list.count()):
            item = self.history_list.item(row)
            if isinstance(item, HistoryEntryItem):
                item.setText(self._row_text(item.result))
