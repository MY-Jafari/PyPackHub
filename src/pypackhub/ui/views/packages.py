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
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import ActionKind, BackendError, CommandPlan, PackageAction
from pypackhub.core.environments import create_environment
from pypackhub.core.inventory import Inventory, InventoryService, PackageRow, action_target_names
from pypackhub.core.metadata import InstalledMetadataIndex, normalize_name
from pypackhub.core.platform_guard import ModificationPolicy, Severity
from pypackhub.core.runner import CommandResult, elevation_supported
from pypackhub.core.snapshots import SnapshotDiff, SnapshotStore, restore_specs
from pypackhub.core.tasks import JobState, TaskResult
from pypackhub.core.validation import ValidationError
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.reasons import reason_text
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.progress import RunProgressTracker
from pypackhub.ui.services import UiContext
from pypackhub.ui.theme.design_tokens import DesignTokens
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.detail_panel import DetailPanel
from pypackhub.ui.widgets.dialogs import (
    BlockedDialog,
    BootstrapNotice,
    ConfirmationDialog,
    CreateEnvironmentDialog,
    CreateEnvironmentRequest,
    DowngradeDialog,
    ElevationNotice,
    FallbackNotice,
    InstallDialog,
    RollbackPreviewDialog,
)
from pypackhub.ui.widgets.log_panel import LogPanel, RunOutcome
from pypackhub.ui.widgets.package_table import (
    COLUMNS,
    ROW_ROLE,
    BadgeDelegate,
    PackageFilterProxy,
    PackageTableModel,
)
from pypackhub.ui.widgets.spinner import Spinner, SpinnerSize
from pypackhub.ui.widgets.toast import ToastKind
from pypackhub.ui.worker_signals import emit_if_alive

__all__ = ["PackagesView", "RefreshOutcome"]


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """What a refresh produced, handed back from the worker thread."""

    inventory: Inventory | None = None
    index: InstalledMetadataIndex | None = None
    error: str = ""
    #: The environment this pass listed, as a string. A pass that started for
    #: one environment must never be applied to another the user switched to
    #: while it ran, so the outcome carries its own identity.
    environment: str = ""


class PackagesView(QWidget):
    """The package table and everything that acts on it."""

    #: Emitted from a worker thread; Qt queues them onto the GUI thread because
    #: the receiver (this widget) lives there.
    row_updated = Signal(object)
    listed = Signal(object)
    refresh_finished = Signal(object)
    run_finished = Signal(object)
    create_finished = Signal(object)
    run_line = Signal(str)
    status_changed = Signal(str)
    toast = Signal(str, object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        log_panel: LogPanel,
        snapshots: SnapshotStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._log = log_panel

        self._environment: Path = context.default_environment
        self._inventory: Inventory | None = None
        self._index: InstalledMetadataIndex | None = None
        self._cancel: threading.Event | None = None
        self._refresh_task: str = ""
        self._refresh_pending = False
        self._run_task: str = ""
        self._create_task: str = ""
        #: Confirmed plans waiting for the running one to finish (a rollback
        #: or an undo can be two commands the user approved as one decision).
        self._plan_queue: list[tuple[CommandPlan, bool]] = []
        #: The snapshot store behind the automatic snapshots and the undo.
        self._snapshots = snapshots if snapshots is not None else SnapshotStore()

        self._spinner = Spinner(size=SpinnerSize.MEDIUM)
        self._spinner.hide()

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

        # Both switches start in the state the settings page stores for them, so
        # "show outdated packages" and "show package sizes" mean something on the
        # next launch instead of resetting themselves every time. They are
        # connected after this, so setting them here does not look like a click.
        self.outdated_check = QCheckBox(self)
        self.outdated_check.setChecked(self._shell.settings.show_outdated_by_default)
        toolbar.addWidget(self.outdated_check)

        self.sizes_check = QCheckBox(self)
        self.sizes_check.setChecked(self._shell.settings.show_sizes_by_default)
        toolbar.addWidget(self.sizes_check)

        toolbar.addStretch(1)

        self._spinner_container = QWidget(self)
        self._spinner_container.setObjectName("spinnerContainer")
        spinner_layout = QHBoxLayout(self._spinner_container)
        spinner_layout.setContentsMargins(0, 0, 0, 0)
        spinner_layout.setSpacing(0)
        self._spinner_container.setVisible(False)
        self._spinner_container.setFixedHeight(28)
        spinner_layout.addWidget(self._spinner)
        toolbar.addWidget(self._spinner_container)

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

        # A package-manager run reports lines, never a percentage a GUI could
        # trust, so this is deliberately the animated kind of progress bar:
        # it says *what* is running and that it is still alive, while the
        # streamed lines in the log carry the detail. Every changing action
        # goes through the one run path below, so an upgrade - which with the
        # log panel closed used to give no sign of life until its toast - is
        # on screen from its first moment like a refresh is.
        self._run_progress = QProgressBar(self)
        self._run_progress.setObjectName("runProgress")
        self._run_progress.setRange(0, 0)  # min == max: Qt's busy animation
        self._run_progress.setTextVisible(True)
        self._run_progress.setVisible(False)
        #: Turns the run's output lines into overall progress; rebuilt by
        #: :meth:`_show_run_progress` for every run.
        self._run_progress_tracker = RunProgressTracker()
        layout.addWidget(self._run_progress)

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
        splitter.setSizes([360, 280])

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
        self.outdated_check.toggled.connect(self._outdated_toggled)
        self.sizes_check.toggled.connect(self._sizes_toggled)
        self.proxy.set_outdated_only(self.outdated_check.isChecked())
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _index: self._update_detail_for_current())

        # These signals are emitted from worker threads; connecting them to slots
        # on this widget makes Qt queue the calls onto the GUI thread.
        self.row_updated.connect(self._on_row_updated)
        self.listed.connect(self._on_listed)
        self.refresh_finished.connect(self._on_refresh_finished)
        self.run_finished.connect(self._on_run_finished)
        self.create_finished.connect(self._on_create_finished)
        self.run_line.connect(self._on_run_line)

    # -- state ---------------------------------------------------------

    @property
    def environment(self) -> Path:
        return self._environment

    @property
    def service(self) -> InventoryService:
        # Asked through the context rather than cached here on purpose: the
        # context keeps one service per environment, and it is the one a
        # package-manager change invalidates. A private copy is how a manager
        # picked in the settings page kept being ignored until the next launch.
        return self._context.service_for(self._environment)

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

        # A pass still running read the *old* environment; letting it finish
        # would paint that environment's packages under the new one's name.
        # It is cancelled rather than waited out, for the same reason a
        # change finishing mid-refresh is (see ``_refresh_after_change``).
        if self._refresh_task:
            self._context.tasks.cancel(self._refresh_task)
            self._refresh_task = ""
            self._refresh_pending = False

        self._environment = resolved
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
            # A refresh is already running. Queue exactly one more instead of
            # dropping this request: the running pass may have read the
            # environment *before* the change that is asking for this refresh,
            # which is how the table kept showing a package's old version after
            # it had been upgraded.
            self._refresh_pending = True
            return

        with_sizes = self.sizes_check.isChecked()
        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_REFRESHING))

        # Captured before the work starts: the pass belongs to the
        # environment it was asked about, not to whatever the view points at
        # when the worker gets there.
        environment = self._environment
        environment_key = str(environment)

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> RefreshOutcome:
            service = self._context.service_for(environment)
            listed: Inventory | None = None

            def remember(inventory: Inventory) -> None:
                """Keep the listing this pass produced, and show it now."""
                nonlocal listed
                listed = inventory
                emit_if_alive(self.listed, inventory)

            try:
                inventory = service.refresh(
                    with_sizes=with_sizes,
                    with_latest=True,
                    on_listed=remember,
                    on_row=lambda row: emit_if_alive(self.row_updated, row),
                    cancel=cancel,
                )
                index = service.metadata_index()
            except Exception as error:
                # The listing may already be on screen even when a later phase
                # failed, so it is handed back with the error rather than thrown
                # away: an index that is unreachable must not empty the table.
                return RefreshOutcome(
                    inventory=listed, error=str(error), environment=environment_key
                )

            if cancel.is_set():
                return RefreshOutcome(
                    inventory=inventory,
                    index=index,
                    error="cancelled",
                    environment=environment_key,
                )

            return RefreshOutcome(inventory=inventory, index=index, environment=environment_key)

        self._refresh_task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.PACKAGES_REFRESHING),
            work,
            on_finish=lambda _task_id, result: emit_if_alive(self.refresh_finished, result),
        )
        # Reveal the spinner container as well as the spinner itself: the spinner
        # lives inside a hidden container, so starting it alone left nothing on
        # screen while the list loaded.
        self._spinner_container.setVisible(True)
        self._spinner.start()
        self.refresh_button.setEnabled(False)

    def _on_refresh_finished(self, result: TaskResult) -> None:
        # A pass that was cancelled - the user switched environments, or a
        # change finished underneath it - still reports back. Its task id no
        # longer matches the running one, and neither its outcome nor its
        # bookkeeping belong to the view anymore: acknowledging either would
        # clobber the pass that took its place.
        if result.task_id != self._refresh_task:
            return

        self._refresh_task = ""
        self._spinner.stop()
        self._spinner_container.setVisible(False)
        self.refresh_button.setEnabled(True)

        try:
            self._apply_refresh_outcome(result)
        finally:
            # A refresh that was asked for while this one was running happens now,
            # once the state this pass read has been rendered. Dropping it was how
            # a change that finished mid-refresh left the table showing the
            # versions from before it.
            if self._refresh_pending:
                self._refresh_pending = False
                self.refresh()

    def _apply_refresh_outcome(self, result: TaskResult) -> None:
        """Render one finished refresh, or explain why there is nothing to render."""
        outcome = result.payload if isinstance(result.payload, RefreshOutcome) else None

        if result.state is JobState.FAILED:
            self.status_changed.emit(result.error)
            self.toast.emit(result.error, ToastKind.ERROR)
            return

        # A pass that listed one environment must not repaint the view while
        # it points at another: the user switched mid-pass, the new pass is
        # already running, and this answer belongs to the old one.
        if (
            outcome is not None
            and outcome.environment
            and outcome.environment != str(self._environment)
        ):
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
        self.status_changed.emit(outcome.error or outcome.inventory.error or "")
        # A pass that listed the environment but could not reach the index still
        # has a usable table; the failure is reported without discarding it.
        if outcome.error and outcome.error != "cancelled":
            self.toast.emit(outcome.error, ToastKind.ERROR)

    def _outdated_toggled(self, checked: bool) -> None:
        """Apply the outdated-only filter, and remember the choice.

        The settings page writes this preference; the toolbar is where it is
        actually used, so the toolbar writes it back.
        """
        self.proxy.set_outdated_only(checked)
        self._shell.apply_settings(replace(self._shell.settings, show_outdated_by_default=checked))
        self._shell.save()

    def _sizes_toggled(self, checked: bool) -> None:
        """Measure sizes on demand, and remember the choice.

        Turning the switch off drops the numbers that were measured with it, so it
        reads as a switch rather than as a one-way door.
        """
        self._shell.apply_settings(replace(self._shell.settings, show_sizes_by_default=checked))
        self._shell.save()

        if not checked:
            self._clear_sizes()
            return

        if self._inventory is None:
            return

        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_MEASURING))
        self.refresh()

    def _clear_sizes(self) -> None:
        """Forget the measured sizes, without reloading anything from disk."""
        if self._inventory is None:
            return

        inventory = self._inventory.replace_rows(
            row.with_size(None) for row in self._inventory.rows
        )
        self._inventory = inventory
        self.model.set_inventory(inventory)
        self._update_summary()

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
        elif self._create_task:
            self._context.tasks.cancel(self._create_task)
            self.status_changed.emit(self._shell.text(TranslationKey.CREATE_ENV_CANCELLED))
        elif self._refresh_task:
            self._context.tasks.cancel(self._refresh_task)
            self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_CANCELLED))

    def create_environment_requested(self) -> bool:
        """Ask where to build a virtual environment, then build it.

        This is the way out of a PEP 668 refusal: the user gets a private
        package folder instead of ``--break-system-packages``. Returns True when
        a creation was scheduled.
        """
        if self._create_task:
            return False

        dialog = CreateEnvironmentDialog(
            translator=self._shell.translator,
            default_location=str(Path.cwd() / ".venv"),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self._run_create(dialog.value())
        return True

    def _run_create(self, request: CreateEnvironmentRequest) -> None:
        """Create the environment on a worker thread, streaming into the log."""
        label = self._shell.text(TranslationKey.CREATE_ENV_TITLE)
        self._log.begin_run(label)
        self._log.append_line(self._shell.text(TranslationKey.CREATE_ENV_RUNNING))
        # A venv creation prints nothing countable before it is done, so the
        # bar for it is the animated one - a determinate bar would sit at
        # zero for the whole run.
        self._show_run_progress(label, determinate=False)

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> object:
            return create_environment(
                Path(request.location).expanduser(),
                interpreter=Path(request.interpreter) if request.interpreter else None,
                cancel=cancel,
                on_progress=lambda line: emit_if_alive(self.run_line, line),
            )

        self._create_task = self._context.tasks.schedule(
            label,
            work,
            on_finish=lambda _task_id, result: emit_if_alive(self.create_finished, result),
        )

    def _on_create_finished(self, result: TaskResult) -> None:
        from pypackhub.core.environments import CreateEnvironmentResult

        self._create_task = ""
        self._hide_run_progress()
        created = result.payload if isinstance(result.payload, CreateEnvironmentResult) else None

        if created is None:
            message = result.error or self._shell.text(TranslationKey.TOAST_FAILED)
            self._log.end_run(RunOutcome.FAILED, error=message)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return

        if created.success:
            message = self._shell.text(
                TranslationKey.CREATE_ENV_CREATED, prefix=str(created.prefix)
            )
            self._log.end_run(RunOutcome.DONE)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.SUCCESS)
            # The new environment is immediately usable, so the window switches
            # to it rather than making the user find it in the switcher.
            self._context.set_default_environment(created.prefix)
            self._environment = Path(created.prefix)
            # Any refresh still running belongs to the environment this one
            # replaces; letting it finish would paint the old environment's
            # packages over the new one's table, so it is stopped first.
            self._refresh_after_change()
            return

        cancelled = created.result.cancelled
        message = (
            self._shell.text(TranslationKey.CREATE_ENV_CANCELLED)
            if cancelled
            else self._shell.text(TranslationKey.CREATE_ENV_FAILED, error=created.message)
        )
        self._log.end_run(
            RunOutcome.CANCELLED if cancelled else RunOutcome.FAILED,
            error="" if cancelled else created.message,
        )
        self.status_changed.emit(message)
        self.toast.emit(
            message,
            ToastKind.INFO if cancelled else ToastKind.ERROR,
        )

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
            # When the refusal is the kind the user can escape - a system or
            # PEP 668 interpreter that only accepts packages in a virtual
            # environment - the dialog carries the way out. Any other refusal
            # stays a plain explanation with no button to click through.
            blocked = BlockedDialog(
                policy=policy,
                translator=self._shell.translator,
                offer_create_environment=_offers_create_environment(policy),
                parent=self,
            )
            blocked.exec()
            if blocked.wants_create_environment:
                self.create_environment_requested()
            return False

        # A missing package manager is not a refusal: with the automatic
        # choice nothing usable means "set pip up", and a manager the user
        # forced in the settings means "set that one up". The action they
        # asked for still happens, through the manager they chose - after the
        # tool that was missing has been installed, with the whole batch
        # shown as commands and confirmed as one decision.
        missing = service.required_backend_missing()
        if missing:
            return self._confirm_bootstrap_and_run(action, policy, missing)

        try:
            # If the backend cannot honour the ``--user`` flag the user asked for,
            # drop it silently rather than surfacing a package-manager error the user
            # cannot act on. pip honours ``--user``; uv does not.
            if action.user and not service.backend.capability.can_install_user:
                action = replace(action, user=False)
                self.toast.emit(
                    self._shell.text(TranslationKey.TOAST_CANCELLED),
                    ToastKind.INFO,
                )
            plan = service.plan(action)
        except ValidationError as error:
            self._report_validation(error)
            return False
        except BackendError as error:
            message = str(error)
            # Translate common package-manager errors into user-friendly text.
            lowered = message.lower()
            if (
                "uv cannot install into the user site directory" in message
                or "unrecognized subcommand" in lowered
            ):
                message = self._shell.text(TranslationKey.ENV_NO_MANAGER)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return False

        # A backend that writes only the environment's own site-packages
        # (uv) cannot change a package whose imported copy lives in the user
        # site: the command would exit 0 and change nothing the user - or the
        # table - can see. Rather than refuse, the same action is offered
        # through pip: the one backend that reaches the shadowing copy, with
        # no administrator privileges involved. The offer is a real
        # confirmation showing pip's own exact command (SPEC section 11.7) -
        # the backend is never substituted silently.
        if not service.backend_can_reach_targets(action):
            return self._confirm_fallback_with_pip(action, policy)

        # The one case elevation exists for: a backend with no user-site path
        # of its own, asked to write an environment the current user may not
        # change. The OS's consent prompt - not this dialog - is what grants
        # the privileges; this decision is only whether to ask for them, and
        # a system that cannot is refused before the user confirms anything.
        needs_elevation = service.backend_needs_elevation(action)
        if needs_elevation and not elevation_supported():
            message = self._shell.text(
                TranslationKey.ELEVATION_UNAVAILABLE, backend=service.backend_name
            )
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.INFO)
            return False

        # ``or needs_elevation``: elevation is confirmed even when the policy
        # alone would not ask, because a run with the OS's help must never be
        # consented to silently (SPEC section 11.5).
        if policy.requires_confirmation or needs_elevation:
            dialog = ConfirmationDialog(
                plan=plan,
                policy=policy,
                translator=self._shell.translator,
                elevation=(
                    ElevationNotice(backend=service.backend_name) if needs_elevation else None
                ),
                parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
                return False

        self._run_plan(plan, elevated=needs_elevation, meta=self._record_action_context(plan))
        return True

    def _confirm_bootstrap_and_run(
        self, action: PackageAction, policy: ModificationPolicy, missing: str
    ) -> bool:
        """Offer to install the missing manager, then run the action through it.

        The commands that install the tool and the action's own command are
        one decision: the confirmation shows every line of the batch (SPEC
        section 11.7), and the plan queue runs them in that order - the
        action's command only starts once the tool it needs exists. uv has
        no ``--user`` path, so a request that asked for one drops the flag
        here rather than planning a command uv would refuse.

        The reachability rule applies before the batch is built, against the
        *missing* manager's semantics: a uv that cannot change the package
        the interpreter imports would "succeed" here exactly as it does when
        it is installed, so the same pip fallback is offered.

        An environment the current user cannot write is the elevation case
        like any other: the whole batch runs with the OS's help, or - when
        the OS cannot provide it - it is refused before anything is queued.
        """
        if missing != "pip" and action.user:
            action = replace(action, user=False)

        if missing != "pip" and not self.service.backend_can_reach_targets(
            action, backend_name=missing
        ):
            return self._confirm_fallback_with_pip(action, policy)

        needs_elevation = self.service.bootstrap_needs_elevation()
        if needs_elevation and not elevation_supported():
            message = self._shell.text(
                TranslationKey.ELEVATION_UNAVAILABLE, backend=self.service.backend_name
            )
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.INFO)
            return False

        try:
            original = self.service.plan_assuming_backend(missing, action)
            bootstrap = self.service.bootstrap_plans(missing, action=action)
        except (BackendError, ValidationError) as error:
            message = str(error)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return False

        if not bootstrap:
            return False

        dialog = ConfirmationDialog(
            plan=original,
            policy=policy,
            translator=self._shell.translator,
            elevation=(ElevationNotice(backend=missing) if needs_elevation else None),
            bootstrap=BootstrapNotice(
                backend=missing,
                commands=tuple(step.display_command() for step in bootstrap),
            ),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
            return False

        # The batch installs a manager: whatever the service probed about the
        # managers is now stale, and the next action must re-probe rather
        # than trust a cached "missing".
        self.service.invalidate_backend_state()
        self.run_confirmed_plans([(step, needs_elevation) for step in (*bootstrap, original)])
        return True

    def _confirm_fallback_with_pip(self, action: PackageAction, policy: ModificationPolicy) -> bool:
        """Offer the same action through pip, the backend that can actually run it.

        The reachability refusal is not "this action cannot happen" - pip can
        upgrade the shadowing copy in the user site, as a normal user - it is
        "the chosen backend cannot run it usefully". So the refusal becomes an
        offer: the identical action, re-planned through pip, shown with pip's
        own exact command and confirmed like any other changing action.

        ``--user`` is part of the rebuilt request for install-style actions:
        the copy that needs changing is in the user site, and pointing pip at
        it explicitly is what guarantees the upgrade lands there - pip's
        automatic fallback would do the same only for as long as the system
        site-packages stays unwritable. Uninstalling has no ``--user`` (pip
        identifies the distribution by name and removes the copy the
        interpreter actually imports), so the request passes through as is.
        """
        if action.kind is not ActionKind.UNINSTALL:
            action = replace(action, user=True)

        try:
            plan = self.service.plan_with_backend("pip", action)
        except BackendError as error:
            message = str(error)
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return False

        dialog = ConfirmationDialog(
            plan=plan,
            policy=policy,
            translator=self._shell.translator,
            fallback=FallbackNotice(replaced=self.service.backend_name),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
            return False

        self._run_plan(plan, meta=self._record_action_context(plan))
        return True

    def _record_action_context(self, plan: CommandPlan) -> dict[str, object]:
        """Record what an approved action is about to change, for the undo.

        SPEC section 4: an automatic snapshot before every risky or bulk
        action. Every changing action gets one here - the file is a few
        kilobytes, and it is what lets the History page offer to put the
        exact versions back for *any* of them, an uninstall included.

        The snapshot is bookkeeping, never a veto: if it cannot be written the
        action still runs, and the undo for it is simply not offered.
        """
        meta: dict[str, object] = {
            "kind": plan.action.kind.value,
            "names": sorted(action_target_names(plan.action)),
            "environment": str(self._environment),
        }

        rows = self._inventory.rows if self._inventory is not None else ()
        if rows:
            try:
                snapshot = self._snapshots.capture(
                    self._environment,
                    [(row.name, row.installed) for row in rows],
                    automatic=True,
                    reason=plan.display_command(),
                )
            except OSError:
                snapshot = None
            if snapshot is not None:
                meta["snapshot"] = snapshot.id

        return meta

    def _show_run_progress(self, label: str, *, determinate: bool = True) -> None:
        """Put the run's label on the bar and reveal it.

        An installing or upgrading run starts at a real zero and fills with
        the work its manager reports (see :meth:`_on_run_line`): 68% of the
        run means 68% of the bar, never a spinner standing in for progress
        nobody can see. A run whose tool prints nothing countable - a
        virtual-environment creation - takes the animated bar instead,
        because a determinate one would sit at zero insisting nothing was
        happening while the tool worked in silence.
        """
        self._run_progress.setFormat(label)
        if determinate:
            self._run_progress_tracker = RunProgressTracker()
            self._run_progress.setRange(0, 100)
            self._run_progress.setValue(0)
        else:
            self._run_progress.setRange(0, 0)
        self._run_progress.setVisible(True)

    def _on_run_line(self, text: str) -> None:
        """Show one output line in the log, and let it move the bar.

        pip and uv report work as it happens - a line per package resolved,
        per file fetched, the switch to the install phase, success - and the
        tracker turns those real events into overall progress. The bar fills
        with what the manager actually did, moves only when a line justifies
        it, and never runs backward; a line that reports nothing leaves it
        exactly where it was.
        """
        self._log.append_line(text)
        if self._run_progress.isHidden():
            return
        percent = self._run_progress_tracker.observe(text)
        if percent is not None:
            self._run_progress.setValue(percent)

    def _hide_run_progress(self) -> None:
        self._run_progress.setVisible(False)

    def _run_plan(
        self,
        plan: CommandPlan,
        *,
        elevated: bool = False,
        meta: dict[str, object] | None = None,
    ) -> None:
        """Run a confirmed plan on a worker thread, streaming its output.

        For install-style actions the backend may supply a download pre-step that
        fetches the targets (and their dependencies) first without installing them.
        That is what lets a package manager be upgraded without trying to replace the
        process that is currently running it: the new wheels are downloaded first,
        then the install runs from the cache.

        An elevated run is the one exception to "streaming its output": the OS's
        consent boundary carries no pipes, so the log says so up front and the
        result arrives when the command ends. ``meta`` is recorded on the task
        result for the History page: what ran, against what names, and the
        snapshot taken before it.
        """
        if self._run_task:
            return

        self._log.begin_run(plan.label)
        self._log.append_line(f"$ {plan.display_command()}")
        if elevated:
            self._log.append_line(self._shell.text(TranslationKey.ELEVATION_NO_OUTPUT))
        self._show_run_progress(plan.label)

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> CommandResult:
            service = self._context.service_for(self._environment)
            backend = service.backend

            preplan = backend.download_plan(plan.action)
            if preplan is not None:
                emit_if_alive(self.run_line, ":: downloading prerequisites ::")
                emit_if_alive(self.run_line, f"$ {preplan.display_command()}")
                pre_result = service.execute(
                    preplan, cancel=cancel, on_line=lambda line: emit_if_alive(self.run_line, line)
                )
                if not pre_result.ok and not cancel.is_set():
                    return pre_result

            return service.execute(
                plan,
                cancel=cancel,
                on_line=lambda line: emit_if_alive(self.run_line, line),
                elevated=elevated,
            )

        self._run_task = self._context.tasks.schedule(
            plan.label,
            work,
            meta=meta,
            on_finish=lambda _task_id, result: emit_if_alive(self.run_finished, result),
        )

    # -- restores, rollbacks and undo -----------------------------------

    def run_confirmed_plans(self, plans: Sequence[tuple[CommandPlan, bool]]) -> None:
        """Run already-confirmed plans one after another.

        A restore is one decision that can be two commands: put the recorded
        versions back, and remove what arrived since. The preview the user
        approved showed every command (SPEC section 11.7), so nothing here
        asks again - they run in order, one undo snapshot taken for the
        whole batch before the first of them starts.
        """
        batch = [entry for entry in plans if entry[0]]
        if not batch:
            return

        meta = self._record_action_context(batch[0][0])
        self._plan_queue.extend(batch)

        # The first plan may start only when nothing else is running; the
        # rest follow in ``_on_run_finished`` as each one completes.
        if not self._run_task:
            plan, elevated = self._plan_queue.pop(0)
            self._run_plan(plan, elevated=elevated, meta=meta)

    def _start_next_confirmed_plan(self) -> None:
        if self._run_task or not self._plan_queue:
            return
        plan, elevated = self._plan_queue.pop(0)
        self._run_plan(plan, elevated=elevated)

    def restore_snapshot(
        self,
        environment: str,
        snapshot_id: str,
        *,
        names: Sequence[str] | None = None,
        empty_key: TranslationKey = TranslationKey.RESTORE_EMPTY,
    ) -> None:
        """Put a recorded state back, after a preview of every command it takes.

        With *names* the restore is an undo: only the packages the recorded
        action touched are considered. Without them it is a full rollback of
        the environment to the snapshot.

        The plans are built before the preview so it can show the exact
        commands (SPEC section 11.7), and the backend that runs them is chosen
        the same way a toolbar action chooses: when the default manager
        cannot reach the packages being restored - the user-site shadowing
        case - pip plans them instead.
        """
        snapshot = self._snapshots.load(environment, snapshot_id)
        if snapshot is None:
            self._report_restore_refusal(empty_key)
            return

        inventory = self._inventory
        if inventory is None or not inventory.rows:
            self._report_restore_refusal(TranslationKey.RESTORE_NOT_READY)
            self.refresh()
            return

        installed = [(row.name, row.installed) for row in inventory.rows]
        diff = self._snapshots.diff(snapshot, installed)

        if names is not None:
            wanted = {normalize_name(name) for name in names}
            removed = tuple(row for row in diff.removed if row[0] in wanted)
            changed = tuple(row for row in diff.changed if row[0] in wanted)
            remove = tuple(name for name in diff.added if name in wanted)
        else:
            removed = diff.removed
            changed = diff.changed
            remove = diff.added

        if not removed and not changed and not remove:
            self._report_restore_refusal(empty_key)
            return

        restore = restore_specs(SnapshotDiff(removed=removed, added=(), changed=changed))
        plans: list[tuple[CommandPlan, bool]] = []

        try:
            if remove:
                plans.append(self._planned_action(self.service.action_for_uninstall_many(remove)))
            if restore:
                plans.append(self._planned_action(self.service.action_for_restore(restore)))
        except (BackendError, ValidationError) as error:
            self.status_changed.emit(str(error))
            self.toast.emit(str(error), ToastKind.ERROR)
            return

        elevated = any(flag for _plan, flag in plans)
        dialog = RollbackPreviewDialog(
            restore_specs=restore,
            remove_names=remove,
            commands=tuple(plan.display_command() for plan, _flag in plans),
            elevated=elevated,
            translator=self._shell.translator,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
            return

        self.run_confirmed_plans(plans)

    def undo_action(self, meta: dict[str, object]) -> None:
        """Reverse one recorded action, using the snapshot it was preceded by.

        The undo is the restore narrowed to the action's own packages:
        whatever the snapshot recorded for those names that no longer matches
        comes back, and what the action installed goes. Without a snapshot -
        or with nothing left to reverse - the entry simply explains that.
        """
        raw_names = cast("Sequence[str]", meta.get("names") or ())
        names = tuple(str(name) for name in raw_names)
        snapshot_id = str(meta.get("snapshot") or "")
        environment = str(meta.get("environment") or "")

        if not names or not snapshot_id:
            self._report_restore_refusal(TranslationKey.HISTORY_UNDO_UNAVAILABLE)
            return

        self.restore_snapshot(
            environment,
            snapshot_id,
            names=names,
            empty_key=TranslationKey.HISTORY_UNDO_UNAVAILABLE,
        )

    def _planned_action(self, action: PackageAction) -> tuple[CommandPlan, bool]:
        """Plan *action* through whichever backend can actually reach it.

        The reachability rule of a toolbar action, applied to restores: the
        default manager plans unless the targets resolve from somewhere it
        cannot write, in which case pip plans and no elevation is involved.
        The bool is the elevation the plan would need, mirroring what
        ``confirm_and_run`` decides for a single action.
        """
        service = self.service
        if not service.backend_can_reach_targets(action):
            return service.plan_with_backend("pip", action), False

        elevated = service.backend_needs_elevation(action)
        if elevated and not elevation_supported():
            # Refuse the same way a confirmed action would: better a clear
            # status line than a plan that cannot run on this system.
            raise BackendError(
                self._shell.text(TranslationKey.ELEVATION_UNAVAILABLE, backend=service.backend_name)
            )
        return service.plan(action), elevated

    def _report_restore_refusal(self, key: TranslationKey) -> None:
        message = self._shell.text(key)
        self.status_changed.emit(message)
        self.toast.emit(message, ToastKind.INFO)

    def _on_run_finished(self, result: TaskResult) -> None:
        self._run_task = ""
        # The next queued plan - if there is one - re-shows the bar with its
        # own label in the same event-loop turn, so there is no flicker.
        self._hide_run_progress()
        command_result = result.payload if isinstance(result.payload, CommandResult) else None

        outcome = _outcome_for(result, command_result)
        error = _error_for(result, command_result)

        self._log.end_run(outcome, error=error)
        if outcome is not RunOutcome.DONE:
            self.status_changed.emit(error)
            self.toast.emit(error or self._shell.text(TranslationKey.TOAST_FAILED), ToastKind.ERROR)
            # A failed or cancelled step stops the batch it belonged to: the
            # plans behind it were approved as one decision, and running the
            # rest anyway would be executing half of what was confirmed.
            self._plan_queue.clear()
            # A failed run may still have changed something before it failed;
            # whatever it left behind is what the next pass has to report.
            self._refresh_after_change()
            return

        self.toast.emit(self._shell.text(TranslationKey.LOG_DONE), ToastKind.SUCCESS)
        # The environment changed, so both the inventory and the metadata index
        # are stale. The index has to be invalidated on the *service* rather than
        # on this view: it is cached per environment and the detail panel, the
        # dependency graph and the size pass all read the cached copy, which is
        # why a package could be upgraded and still be described by its old
        # metadata everywhere except the packages table.
        self.service.invalidate_metadata()
        self._index = None
        self._refresh_after_change()
        self._start_next_confirmed_plan()

    def _refresh_after_change(self) -> None:
        """Reload now that the environment changed, overtaking a stale pass.

        A refresh that is still running read the environment *before* the
        change, so waiting it out only delays the answer the user is looking
        at the table for - an upgrade that finished behind a big, slow pass
        could leave the old version on screen for minutes. The stale pass is
        cancelled instead; the pending-refresh mechanism then starts the fresh
        one the moment the old pass stops, and its partial results are replaced
        by the new listing before anyone can mistake them for current.
        """
        if self._refresh_task:
            self._context.tasks.cancel(self._refresh_task)
        self.refresh()

    # ---- installation / uninstall / upgrade actions --------------------------------

    def _change_selected(self, kind: ActionKind) -> None:
        """Run one action for the current selection (or all outdated packages)."""
        rows = self.selected_rows()

        if kind is ActionKind.UPGRADE_ALL:
            if self._inventory is None or not self._inventory.outdated_count:
                self.toast.emit(self._shell.text(TranslationKey.LOG_EMPTY), ToastKind.INFO)
                return
            try:
                action = self.service.action_for_upgrade_all(
                    self._inventory, user=self._may_use_user_flag()
                )
            except ValidationError as error:
                self._report_validation(error)
                return
            self.confirm_and_run(action)
            return

        if len(rows) != 1:
            self._need_single_selection()
            return

        row = rows[0]
        user = self._may_use_user_flag()

        # The install and downgrade paths already guard this; the builders
        # re-validate names, and one installed row the validator refuses must
        # be reported, not raise out of the click handler.
        try:
            if kind is ActionKind.UNINSTALL:
                action = self.service.action_for_uninstall(row.name)
            else:
                action = self.service.action_for_upgrade(row.name, user=user)
        except ValidationError as error:
            self._report_validation(error)
            return
        self.confirm_and_run(action)

    def _may_use_user_flag(self) -> bool:
        """Whether ``--user`` is meaningful here.

        It is not inside an isolated environment: pip refuses a ``--user``
        install there, so offering the checkbox would only produce a confusing
        failure. Base interpreters are exactly where it matters. It is also only
        offered when the active backend can actually honour it.
        """
        facts = self.service.facts
        if facts.is_isolated:
            return False
        backend = self.service.backend
        return backend.capability.can_install_user

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
        # If the same row is still selected, tell the panel about any version/metadata
        # that changed so the title and version line reflect the latest state.
        self.detail._refresh_from_row(row, metadata=metadata)

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

    def _on_listed(self, inventory: Inventory) -> None:
        """Show what is installed the moment the listing exists.

        Listing is local and fast; the latest versions behind it are not. Filling
        the table here is what makes a refresh feel immediate - the packages are
        on screen while the index lookups catch up, instead of an empty table
        behind a spinner until the slowest of them answers.

        A listing that arrives for an environment the view no longer points at
        is dropped: the user switched mid-pass, and the pass for the new
        environment is the one that should paint it.
        """
        if inventory.environment != self._environment:
            return
        self._inventory = inventory
        self.model.set_inventory(inventory)
        self._update_summary()
        self._update_empty_state()
        self._update_buttons()
        self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_LOADING_LATEST))

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


def _offers_create_environment(policy: ModificationPolicy) -> bool:
    """Whether a refusal is one a virtual environment would actually solve.

    That is the externally-managed / system-interpreter case: the interpreter
    will happily install into a virtual environment, so offering to create one is
    a real answer rather than a distraction. An App Execution Alias stub is not,
    because there is no interpreter behind it to build an environment with.
    """
    return policy.would_need_installer_override


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
