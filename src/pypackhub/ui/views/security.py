"""The security view: scan the installed packages against OSV.dev.

A scan asks the vulnerability database about every installed ``(name,
version)`` pair and nothing else (SPEC section 11.4) - no paths, no
hostnames, no versions of anything but the package asked about. The scan is
strictly user-initiated: it starts when the button is pressed, its findings
fill the list as they arrive, and the time of the last run is shown so a
stale answer can be recognised as one.

"Upgrade to fix" never runs anything here: the view emits the same validated
upgrade action the toolbar uses, and the window routes it to the packages
view's confirm-and-run path - where the automatic snapshot, the confirmation
with the exact command and (on this user's machine) the pip fallback all
apply. Fixing a vulnerability is an upgrade like any other; it just has a
better reason.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

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

from pypackhub.core.backends.base import ActionKind, PackageAction
from pypackhub.core.security import OsvClient, ScanReport, Vulnerability
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.links import open_external_url
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.spinner import Spinner, SpinnerSize
from pypackhub.ui.widgets.toast import ToastKind

__all__ = ["SecurityView"]

#: The severity words OSV/GHSA use, mapped to the badge keys the view shows.
_SEVERITY_KEYS = {
    "low": TranslationKey.SECURITY_LOW,
    "moderate": TranslationKey.SECURITY_MEDIUM,
    "medium": TranslationKey.SECURITY_MEDIUM,
    "high": TranslationKey.SECURITY_HIGH,
    "critical": TranslationKey.SECURITY_CRITICAL,
}


class VulnerabilityItem(QListWidgetItem):
    """One advisory, carrying its finding for the fix button."""

    def __init__(self, finding: Vulnerability, text: str) -> None:
        super().__init__()
        self.finding = finding
        self.setText(text)


class SecurityView(QWidget):
    """Scan for known vulnerabilities, and upgrade past the fixable ones."""

    #: A validated upgrade action for a vulnerable package. The window routes
    #: it to the packages view's confirm-and-run path.
    fix_requested = Signal(object)
    status_changed = Signal(str)
    toast = Signal(str, object)

    #: From worker threads; Qt queues them onto the GUI thread.
    scan_finished = Signal(object)
    finding_arrived = Signal(object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        osv_client: OsvClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._client = osv_client
        self._task = ""
        self._report: ScanReport | None = None

        self.setObjectName("securityPanel")

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

        self.scan_button = QPushButton(self)
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.clicked.connect(self._on_scan_clicked)
        controls.addWidget(self.scan_button)

        self.fix_button = QPushButton(self)
        self.fix_button.setEnabled(False)
        self.fix_button.clicked.connect(self._upgrade_selected)
        controls.addWidget(self.fix_button)

        self.advisory_button = QPushButton(self)
        self.advisory_button.setEnabled(False)
        self.advisory_button.clicked.connect(self._open_selected_advisory)
        controls.addWidget(self.advisory_button)

        controls.addStretch(1)
        self.spinner = Spinner(size=SpinnerSize.SMALL)
        self.spinner.hide()
        controls.addWidget(self.spinner)
        layout.addLayout(controls)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("secondaryText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.results_label = QLabel(self)
        self.results_label.setObjectName("sectionHeading")
        layout.addWidget(self.results_label)

        self.findings_list = QListWidget(self)
        self.findings_list.setObjectName("vulnList")
        self.findings_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.findings_list.itemDoubleClicked.connect(lambda _item: self._open_selected_advisory())
        layout.addWidget(self.findings_list, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        self.retranslate()
        self.scan_finished.connect(self._on_scan_finished)
        self.finding_arrived.connect(self._on_finding)

    # -- scanning -------------------------------------------------------

    @property
    def client(self) -> OsvClient:
        if self._client is None:
            self._client = OsvClient()
        return self._client

    @property
    def report(self) -> ScanReport | None:
        """The last completed scan, kept so the page can be reopened offline."""
        return self._report

    @property
    def vulnerable_count(self) -> int:
        """How many distinct packages the last scan found affected."""
        return len(self._report.vulnerable_packages) if self._report is not None else 0

    def _on_scan_clicked(self) -> None:
        if self._task:
            self._context.tasks.cancel(self._task)
            return
        self.start_scan()

    def start_scan(self) -> None:
        """Scan every installed package, filling findings in as they arrive."""
        if self._task:
            return

        self.findings_list.clear()
        self.empty_label.setVisible(False)
        self._report = None
        self._set_scanning(True)
        self.status_changed.emit(self._shell.text(TranslationKey.SECURITY_SCANNING))

        client = self.client
        context = self._context

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> ScanReport:
            service = context.service_for(context.default_environment)
            inventory = service.list()
            pairs = [(row.name, row.installed) for row in inventory.rows if row.installed]
            return client.scan(pairs, cancel=cancel, on_finding=self.finding_arrived.emit)

        self._task = context.tasks.schedule(
            self._shell.text(TranslationKey.SECURITY_TITLE),
            work,
            on_finish=lambda _task_id, result: self.scan_finished.emit(result),
        )

    def _set_scanning(self, scanning: bool) -> None:
        self.scan_button.setText(
            self._shell.text(
                TranslationKey.SECURITY_STOP_SCAN
                if scanning
                else TranslationKey.SECURITY_START_SCAN
            )
        )
        self.scan_button.setEnabled(True)
        if scanning:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.spinner.setVisible(scanning)

    def _on_finding(self, finding: Vulnerability) -> None:
        """One advisory arrived; show it the moment it is known."""
        self.findings_list.addItem(VulnerabilityItem(finding, self._finding_text(finding)))

    def _on_scan_finished(self, result: Any) -> None:
        self._task = ""
        self._set_scanning(False)

        report = result.payload if isinstance(result.payload, ScanReport) else None
        if report is None:
            error = getattr(result, "error", "") or self._shell.text(
                TranslationKey.SECURITY_SCANNING_ERROR
            )
            message = f"{self._shell.text(TranslationKey.SECURITY_SCANNING_ERROR)}: {error}"
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return

        self._report = report
        self._render_report(report)

    def _render_report(self, report: ScanReport) -> None:
        self.findings_list.clear()
        for finding in report.findings:
            self._on_finding(finding)

        status = self._shell.text(
            TranslationKey.SECURITY_LAST_SCAN,
            time=datetime.fromtimestamp(report.timestamp).strftime("%H:%M:%S"),
        )
        if report.offline:
            status = f"{status}\n{self._shell.text(TranslationKey.SECURITY_SCAN_OFFLINE)}"
        self.status_label.setText(status)
        self.status_changed.emit(status)

        if not report.findings:
            self.empty_label.setText(self._shell.text(TranslationKey.SECURITY_NO_VULNERABILITIES))
            self.empty_label.setVisible(True)
        else:
            self.empty_label.setVisible(False)

    def _finding_text(self, finding: Vulnerability) -> str:
        severity_key = _SEVERITY_KEYS.get(finding.severity, TranslationKey.SECURITY_UNKNOWN)
        severity = self._shell.text(severity_key)
        text = f"{finding.package} ({finding.installed_version}) — {severity}"
        if finding.fixed_in:
            text += f" — {self._shell.text(TranslationKey.SECURITY_FIXED_IN)}: {finding.fixed_in}"
        return text

    # -- the selected finding -------------------------------------------

    def _selected_finding(self) -> Vulnerability | None:
        selected = self.findings_list.selectedItems()
        if not selected or not isinstance(selected[0], VulnerabilityItem):
            return None
        return selected[0].finding

    def _on_selection_changed(self) -> None:
        finding = self._selected_finding()
        self.fix_button.setEnabled(finding is not None and bool(finding.fixed_in))
        self.advisory_button.setEnabled(finding is not None)

    def _upgrade_selected(self) -> None:
        """Emit the upgrade action for the package behind the selected finding."""
        finding = self._selected_finding()
        if finding is None:
            return

        self.fix_requested.emit(
            PackageAction(
                kind=ActionKind.UPGRADE,
                environment=self._context.default_environment,
                names=(finding.package,),
            )
        )

    def _open_selected_advisory(self) -> None:
        """Open the advisory in the system browser - HTTPS only, on click."""
        finding = self._selected_finding()
        if finding is None:
            return
        open_external_url(finding.url)

    # -- chrome --------------------------------------------------------

    def retranslate(self) -> None:
        shell = self._shell
        scanning = bool(self._task)
        self.title_label.setText(shell.text(TranslationKey.SECURITY_TITLE))
        self.intro_label.setText(shell.text(TranslationKey.SECURITY_INTRO))
        self.scan_button.setText(
            shell.text(
                TranslationKey.SECURITY_STOP_SCAN
                if scanning
                else TranslationKey.SECURITY_START_SCAN
            )
        )
        self.fix_button.setText(shell.text(TranslationKey.SECURITY_FIX))
        self.advisory_button.setText(shell.text(TranslationKey.SECURITY_OPEN_ADVISORY))
        self.results_label.setText(shell.text(TranslationKey.SECURITY_RESULTS))
        self.empty_label.setText(shell.text(TranslationKey.SECURITY_NO_VULNERABILITIES))

        if self._report is not None:
            self._render_report(self._report)

    def refresh(self) -> None:
        """Kept for navigation symmetry: the last report stays until rescanned."""
