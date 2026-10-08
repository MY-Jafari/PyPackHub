"""The packages view: the table, its actions and the confirmations around them.

This is the view the whole application is built around in Phase 1. It shows what
is installed, what is behind PyPI, and how much space it takes; it performs the
five changing actions behind a confirmation that shows the exact command; and it
streams every action into the log panel so nothing happens silently.

Three rules are enforced here rather than hoped for:

* **Nothing changes without a confirmation.** Every action goes through
  :meth:`PackagesView.confirm_and_run`, which asks the core for the plan, asks the
  platform guard whether this environment may be changed at all, shows the plan's
  own command text, and only then hands the *same* plan to the worker.
* **The UI never blocks.** Listing, PyPI lookups, size measurement, metadata
  scanning and every install run on a ``core.tasks`` worker thread; results reach
  the window as Qt signals, which Qt queues onto the GUI thread.
* **A refusal explains itself.** A system or PEP 668 interpreter is refused with
  the reason and a suggestion, in the current language, instead of a failed
  command and a stack of pip output.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import ActionKind, BackendError, CommandPlan, PackageAction
from pypackhub.core.inventory import Inventory, InventoryService, PackageRow
from pypackhub.core.metadata import InstalledMetadataIndex
from pypackhub.core.platform_guard import ModificationPolicy, Severity
from pypackhub.core.runner import CommandResult
from pypackhub.core.tasks import JobState, TaskResult
from pypackhub.core.validation import ValidationError
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.reasons import reason_text
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.theme.design_tokens import DesignTokens
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.detail_panel import DetailPanel
from pypackhub.ui.widgets.dialogs import (
    BlockedDialog,
    ConfirmationDialog,
    DowngradeDialog,
    InstallDialog,
)
from pypackhub.ui.widgets.log_panel import LogPanel, RunOutcome
from pypackhub.ui.widgets.package_table import (
    COLUMNS,
    ROW_ROLE,
    BadgeDelegate,
    PackageFilterProxy,
    PackageTableModel,
)
from pypackhub.ui.widgets.toast import ToastKind

__all__ = ["PackagesView", "RefreshOutcome"]


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """What a refresh produced, handed back from the worker thread."""

    inventory: Inventory | None = None
    index: InstalledMetadataIndex | None = None
    error: str = ""


class PackagesView(QWidget):
    """The package table and everything that acts on it."""

    #: Emitted from a worker thread; Qt queues them onto the GUI thread because
    #: the receiver (this widget) lives there.
    row_updated = Signal(object)
    refresh_finished = Signal(object)
    run_finished = Signal(object)
    run_line = Signal(str)
    status_changed = Signal(str)
    toast = Signal(str, object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        log_panel: LogPanel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._log = log_panel

        self._environment: Path = context.default_environment
        self._service: InventoryService | None = None
        self._inventory: Inventory | None = None
        self._index: InstalledMetadataIndex | None = None
        self._cancel: threading.Event | None = None
        self._refresh_task: str = ""
        self._run_task: str = ""

        self._build_ui()
        self._connect()

    # -- construction --------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("pageTitle")
        header.addWidget(self.title_label)

        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("secondaryText")
        header.addWidget(self.summary_label)
        header.addStretch(1)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        toolbar.addWidget(self.search_edit)

        self.outdated_check = QCheckBox(self)
        toolbar.addWidget(self.outdated_check)

        self.sizes_check = QCheckBox(self)
        toolbar.addWidget(self.sizes_check)

        toolbar.addStretch(1)

        self.refresh_button = QPushButton(self)
        self.refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_button)

        self.install_button = QPushButton(self)
        self.install_button.setObjectName("primaryButton")
        self.install_button.clicked.connect(self.install_requested)
        toolbar.addWidget(self.install_button)

        self.upgrade_button = QPushButton(self)
        self.upgrade_button.clicked.connect(lambda: self._change_selected(ActionKind.UPGRADE))
        toolbar.addWidget(self.upgrade_button)

        self.downgrade_button = QPushButton(self)
        self.downgrade_button.clicked.connect(self.downgrade_requested)
        toolbar.addWidget(self.downgrade_button)

        self.uninstall_button = QPushButton(self)
        self.uninstall_button.setObjectName("dangerButton")
        self.uninstall_button.clicked.connect(lambda: self._change_selected(ActionKind.UNINSTALL))
        toolbar.addWidget(self.uninstall_button)

        self.upgrade_all_button = QPushButton(self)
        self.upgrade_all_button.clicked.connect(
            lambda: self._change_selected(ActionKind.UPGRADE_ALL)
        )
        toolbar.addWidget(self.upgrade_all_button)

        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        table_container = QWidget(splitter)
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        self.table = QTableView(table_container)
        self.table.setObjectName("packageTable")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table_layout.addWidget(self.table, 1)

        self.empty_label = QLabel(table_container)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        table_layout.addWidget(self.empty_label)

        splitter.addWidget(table_container)

        self.detail = DetailPanel(translator=self._shell.translator, parent=splitter)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        self.model = PackageTableModel(
            translator=self._shell.translator,
            tokens=self._shell.active_theme.tokens,
            parent=self,
        )
        self.proxy = PackageFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table.setModel(self.proxy)
        self.table.setItemDelegateForColumn(
            next(index for index, (column, _, _) in enumerate(COLUMNS) if column.value == "status"),
            BadgeDelegate(self.table),
        )

        header_view = self.table.horizontalHeader()
        for index, (_, _, width) in enumerate(COLUMNS):
            if width:
                self.table.setColumnWidth(index, width)
            else:
                header_view.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        header_view.setSortIndicator(0, Qt.SortOrder.AscendingOrder)

    def _connect(self) -> None:
        self.search_edit.textChanged.connect(self.proxy.set_query)
        self.outdated_check.toggled.connect(self.proxy.set_outdated_only)
        self.sizes_check.toggled.connect(self._sizes_toggled)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _index: self._update_detail_for_current())

        # These signals are emitted from worker threads; connecting them to slots
        # on this widget makes Qt queue the calls onto the GUI thread.
        self.row_updated.connect(self._on_row_updated)
        self.refresh_finished.connect(self._on_refresh_finished)
        self.run_finished.connect(self._on_run_finished)
        self.run_line.connect(self._log.append_line)

    # -- state ---------------------------------------------------------

    @property
    def environment(self) -> Path:
        return self._environment

    @property
    def service(self) -> InventoryService:
        if self._service is None:
            self._service = self._context.service_for(self._environment)
        return self._service

    @property
    def inventory(self) -> Inventory | None:
        return self._inventory

    @property
    def is_busy(self) -> bool:
        return bool(self._refresh_task or self._run_task)

    def set_environment(self, prefix: Path) -> None:
        """Point the view at another environment and reload it."""
        resolved = Path(prefix).resolve()
        if resolved == self._environment and self._inventory is not None:
            return

        self._environment = resolved
        self._service = None
        self._inventory = None
        self._index = None
        self.model.set_inventory(None)
        self.detail.clear()
        self._context.set_default_environment(resolved)
        self.refresh()

    def selected_rows(self) -> tuple[PackageRow, ...]:
        """The distinct selected rows, in table order."""
        selection = self.table.selectionModel()
        if selection is None:
            return ()
        return self.proxy.selected_rows(list(selection.selectedRows()))

    def visible_rows(self) -> tuple[PackageRow, ...]:
        """The rows currently shown, in the proxy's order."""
        rows: list[PackageRow] = []
        for row_index in range(self.proxy.rowCount()):
            row = self.proxy.index(row_index, 0).data(ROW_ROLE)
            if isinstance(row, PackageRow):
                rows.append(row)
        return tuple(rows)

    # -- refreshing ----------------------------------------------------

    def refresh(self) -> None:
        """Reload the environment: listing now, PyPI and sizes progressively."""
        if self._refresh_task:
            return

        with_sizes = self.sizes_check.isChecked()
        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_REFRESHING))

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> RefreshOutcome:
            service = self._context.service_for(self._environment)
            try:
                inventory = service.refresh(
                    with_sizes=with_sizes,
                    with_latest=True,
                    on_row=self.row_updated.emit,
                    cancel=cancel,
                )
                index = service.metadata_index()
            except Exception as error:
                return RefreshOutcome(error=str(error))

            if cancel.is_set():
                return RefreshOutcome(inventory=inventory, index=index, error="cancelled")

            return RefreshOutcome(inventory=inventory, index=index)

        self._refresh_task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.PACKAGES_REFRESHING),
            work,
            on_finish=lambda _task_id, result: self.refresh_finished.emit(result),
        )

    def _on_refresh_finished(self, result: TaskResult) -> None:
        self._refresh_task = ""
        outcome = result.payload if isinstance(result.payload, RefreshOutcome) else None

        if result.state is JobState.FAILED:
            self.status_changed.emit(result.error)
            self.toast.emit(result.error, ToastKind.ERROR)
            return

        if outcome is None or outcome.inventory is None:
            error = outcome.error if outcome is not None else result.error
            self.model.set_inventory(None)
            self._update_empty_state()
            self.status_changed.emit(error)
            if error and error != "cancelled":
                self.toast.emit(error, ToastKind.ERROR)
            return

        self._inventory = outcome.inventory
        self._index = outcome.index
        self.model.set_inventory(outcome.inventory)
        self._update_summary()
        self._update_empty_state()
        self._update_detail_for_current()
        self.status_changed.emit(outcome.inventory.error or "")

    def _sizes_toggled(self, checked: bool) -> None:
        """Measure sizes on demand, without a second network pass."""
        if not checked or self._inventory is None:
            return
        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_MEASURING))
        self.refresh()

    # -- actions -------------------------------------------------------

    def install_requested(self) -> None:
        """Ask for a specifier, then confirm and install it."""
        dialog = InstallDialog(
            translator=self._shell.translator,
            allow_user=self._may_use_user_flag(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        request = dialog.value()
        if not request.spec:
            return

        try:
            action = self.service.action_for_install(request.spec, user=request.user)
        except ValidationError as error:
            self._report_validation(error)
            return

        self.confirm_and_run(action)

    def downgrade_requested(self) -> None:
        """Ask for a target version, then confirm and install exactly that pin."""
        rows = self.selected_rows()
        if len(rows) != 1:
            self._need_single_selection()
            return

        row = rows[0]
        dialog = DowngradeDialog(
            package=row.name,
            current_version=row.installed,
            translator=self._shell.translator,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            action = self.service.action_for_downgrade(
                row.name, dialog.version, user=self._may_use_user_flag()
            )
        except ValidationError as error:
            self._report_validation(error)
            return

        self.confirm_and_run(action)

    def cancel_running(self) -> None:
        """Ask the running task to stop; the process tree is terminated."""
        if self._run_task:
            self._context.tasks.cancel(self._run_task)
            self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_CANCELLED))
        elif self._refresh_task:
            self._context.tasks.cancel(self._refresh_task)
            self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_CANCELLED))

    def confirm_and_run(self, action: PackageAction) -> bool:
        """The one path that changes an environment.

        Returns True when the action was approved and scheduled. Every caller -
        the toolbar, the detail panel, a future command palette - goes through
        here, so the policy check and the confirmation cannot be bypassed by
        accident.

        A refusal is final for the cases the spec makes non-negotiable: a
        PEP 668 interpreter (which would need ``--break-system-packages``) and an
        App Execution Alias stub. A base interpreter is a warning, not a refusal -
        the user is told what it means and has to confirm it explicitly.
        """
        service = self.service

        policy = service.policy()
        if _is_blocked(policy):
            BlockedDialog(policy=policy, translator=self._shell.translator, parent=self).exec()
            return False

        try:
            plan = service.plan(action)
        except ValidationError as error:
            self._report_validation(error)
            return False
        except BackendError as error:
            self.status_changed.emit(str(error))
            self.toast.emit(str(error), ToastKind.ERROR)
            return False

        if policy.requires_confirmation:
            dialog = ConfirmationDialog(
                plan=plan,
                policy=policy,
                translator=self._shell.translator,
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
                return False

        self._run_plan(plan)
        return True

    def _run_plan(self, plan: CommandPlan) -> None:
        """Run a confirmed plan on a worker thread, streaming its output."""
        if self._run_task:
            return

        self._log.begin_run(plan.label)
        self._log.append_line(f"$ {plan.display_command()}")

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> CommandResult:
            service = self._context.service_for(self._environment)
            return service.execute(plan, cancel=cancel, on_line=self.run_line.emit)

        self._run_task = self._context.tasks.schedule(
            plan.label,
            work,
            on_finish=lambda _task_id, result: self.run_finished.emit(result),
        )

    def _on_run_finished(self, result: TaskResult) -> None:
        self._run_task = ""
        command_result = result.payload if isinstance(result.payload, CommandResult) else None

        outcome = _outcome_for(result, command_result)
        error = _error_for(result, command_result)

        self._log.end_run(outcome, error=error)
        if outcome is not RunOutcome.DONE:
            self.status_changed.emit(error)
            self.toast.emit(error or self._shell.text(TranslationKey.TOAST_FAILED), ToastKind.ERROR)
            self.refresh()
            return

        self.toast.emit(self._shell.text(TranslationKey.LOG_DONE), ToastKind.SUCCESS)
        # The environment changed, so both the metadata index and the inventory
        # are stale: the next refresh recomputes them.
        self._index = None
        self.refresh()

    def _change_selected(self, kind: ActionKind) -> None:
        """Run one action for the current selection (or all outdated packages)."""
        rows = self.selected_rows()

        if kind is ActionKind.UPGRADE_ALL:
            if self._inventory is None or not self._inventory.outdated_count:
                self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_NO_SELECTION))
                return
            self.confirm_and_run(
                self.service.action_for_upgrade_all(self._inventory, user=self._may_use_user_flag())
            )
            return

        if len(rows) != 1:
            self._need_single_selection()
            return

        row = rows[0]
        user = self._may_use_user_flag()
        if kind is ActionKind.UNINSTALL:
            action = self.service.action_for_uninstall(row.name)
        else:
            action = self.service.action_for_upgrade(row.name, user=user)
        self.confirm_and_run(action)

    def _may_use_user_flag(self) -> bool:
        """Whether ``--user`` is meaningful here.

        It is not inside an isolated environment: pip refuses a ``--user``
        install there, so offering the checkbox would only produce a confusing
        failure. Base interpreters are exactly where it matters.
        """
        facts = self.service.facts
        return not facts.is_isolated

    # -- detail panel --------------------------------------------------

    def _selection_changed(self, *_args: object) -> None:
        self._update_detail_for_current()
        self._update_buttons()

    def _update_detail_for_current(self) -> None:
        rows = self.selected_rows()
        if not rows:
            self.detail.clear()
            return

        row = rows[0]
        metadata = None
        dependents: tuple[str, ...] = ()
        if self._index is not None:
            metadata = self._index.get(row.name)
            dependents = self._index.dependents_of(row.name)

        self.detail.show_row(row, metadata=metadata, dependents=dependents)

    def _update_buttons(self) -> None:
        rows = self.selected_rows()
        single = len(rows) == 1
        self.upgrade_button.setEnabled(single)
        self.downgrade_button.setEnabled(single)
        self.uninstall_button.setEnabled(single)
        self.upgrade_all_button.setEnabled(bool(self._inventory and self._inventory.outdated_count))

        selected_text = ""
        if rows:
            selected_text = self._shell.text(TranslationKey.PACKAGES_SELECTED, count=len(rows))
        self.summary_label.setText(selected_text or self._summary_text())

    # -- rendering -----------------------------------------------------

    def apply_theme(self, tokens: DesignTokens) -> None:
        """Re-render badge colours after a theme switch."""
        self.model.set_tokens(tokens)

    def retranslate(self) -> None:
        """Re-render every string after a language switch."""
        shell = self._shell
        self.title_label.setText(shell.text(TranslationKey.PACKAGES_TITLE))
        self.search_edit.setPlaceholderText(shell.text(TranslationKey.PACKAGES_SEARCH))
        self.outdated_check.setText(shell.text(TranslationKey.PACKAGES_ONLY_OUTDATED))
        self.sizes_check.setText(shell.text(TranslationKey.PACKAGES_SHOW_SIZES))
        self.refresh_button.setText(shell.text(TranslationKey.PACKAGES_REFRESH))
        self.install_button.setText(shell.text(TranslationKey.PACKAGES_INSTALL))
        self.upgrade_button.setText(shell.text(TranslationKey.PACKAGES_UPGRADE))
        self.downgrade_button.setText(shell.text(TranslationKey.PACKAGES_DOWNGRADE))
        self.uninstall_button.setText(shell.text(TranslationKey.PACKAGES_UNINSTALL))
        self.upgrade_all_button.setText(shell.text(TranslationKey.PACKAGES_UPGRADE_ALL))
        self.model.set_translator(shell.translator)
        self.detail.retranslate(shell.translator)
        self._update_summary()
        self._update_empty_state()
        self._update_buttons()

    def _summary_text(self) -> str:
        if self._inventory is None:
            return ""
        parts = [
            self._shell.text(TranslationKey.PACKAGES_COUNT, count=len(self._inventory.rows)),
            self._shell.text(
                TranslationKey.PACKAGES_OUTDATED_TOTAL, count=self._inventory.outdated_count
            ),
        ]
        if self._inventory.measured_size_count:
            parts.append(
                self._shell.text(
                    TranslationKey.PACKAGES_TOTAL_SIZE,
                    size=format_bytes(self._inventory.total_size_bytes),
                )
            )
        return "  \u00b7  ".join(parts)

    def _update_summary(self) -> None:
        self.summary_label.setText(self._summary_text())

    def _update_empty_state(self) -> None:
        if self._inventory is None:
            message = ""
        elif not self._inventory.rows:
            message = self._shell.text(TranslationKey.PACKAGES_EMPTY)
        elif self.proxy.rowCount() == 0:
            message = self._shell.text(TranslationKey.PACKAGES_EMPTY_FILTER)
        else:
            message = ""

        self.empty_label.setText(message)
        self.empty_label.setVisible(bool(message))
        self.table.setVisible(bool(self._inventory and self._inventory.rows))

    def _on_row_updated(self, row: PackageRow) -> None:
        """A single package finished resolving; update just that row."""
        self.model.update_row(row)
        self._update_summary()

    # -- helpers -------------------------------------------------------

    def _need_single_selection(self) -> None:
        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_NO_SELECTION))

    def _report_validation(self, error: ValidationError) -> None:
        message = reason_text(
            str(error.reason),
            self._shell.translator.resolved_language,
            fallback=str(error),
        )
        self.status_changed.emit(message)
        self.toast.emit(message, ToastKind.ERROR)

    def select_first_row(self) -> None:
        """Select the first visible row, for keyboard-driven and test flows."""
        if self.proxy.rowCount() == 0:
            return
        self.table.selectRow(0)


def _is_blocked(policy: ModificationPolicy) -> bool:
    """Whether the spec makes this refusal non-negotiable.

    A PEP 668 interpreter would require ``--break-system-packages`` (which
    PyPackHub never passes), and an App Execution Alias stub is not an
    interpreter at all. A base interpreter is a *warning*: it is still the user's
    machine, so it is confirmed rather than forbidden.
    """
    if policy.allowed:
        return False
    return policy.would_need_installer_override or policy.severity is Severity.DANGER


def _outcome_for(result: TaskResult, command_result: CommandResult | None) -> RunOutcome:
    if result.state is JobState.CANCELLED or (command_result and command_result.cancelled):
        return RunOutcome.CANCELLED
    if result.state is JobState.FAILED or (command_result and not command_result.ok):
        return RunOutcome.FAILED
    return RunOutcome.DONE


def _error_for(result: TaskResult, command_result: CommandResult | None) -> str:
    if command_result is not None:
        if command_result.cancelled:
            return ""
        if command_result.timed_out:
            return "timed out"
        if not command_result.ok:
            return command_result.output.strip() or result.error
    return result.error
