"""Security view for PyPackHub.

This module renders the security page showing vulnerability scan results
with severity levels, affected packages, and details.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

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


class Severity(Enum):
    """Vulnerability severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class Vulnerability:
    """A security vulnerability finding."""

    package: str
    version: str
    severity: Severity
    description: str
    fixed_in: str | None = None
    cve_id: str | None = None
    details: str | None = None


@dataclass
class ScanResult:
    """Result of a security scan."""

    vulnerabilities: list[Vulnerability]
    scanned_at: datetime
    error: str | None = None


class SecurityView(QWidget):
    """A security widget for vulnerability scanning and results."""

    # Emitted when scan starts/stops
    scan_started = Signal()
    scan_finished = Signal(ScanResult)
    scan_error = Signal(str)

    def __init__(
        self,
        *,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._scanning = False
        self._results: ScanResult | None = None
        self._vulnerabilities: list[Vulnerability] = []

        self.setObjectName("securityPanel")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Header
        self._title_label = QLabel(self)
        self._title_label.setObjectName("pageTitle")
        self._title_label.setText(translator.translate(TranslationKey.SECURITY_TITLE))
        layout.addWidget(self._title_label)

        # Scan button
        self._scan_button = QPushButton(self)
        self._scan_button.setObjectName("primaryButton")
        self._scan_button.setText(translator.translate(TranslationKey.SECURITY_START_SCAN))
        self._scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scan_button.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self._scan_button)

        # Status label
        self._status_label = QLabel(self)
        self._status_label.setObjectName("secondaryText")
        layout.addWidget(self._status_label)

        # Results label
        self._results_label = QLabel(self)
        self._results_label.setObjectName("secondaryText")
        self._results_label.setText(translator.translate(TranslationKey.SECURITY_RESULTS))
        layout.addWidget(self._results_label)

        # Vulnerabilities list
        self._vuln_list = QListWidget(self)
        self._vuln_list.setObjectName("vulnList")
        layout.addWidget(self._vuln_list)

        # Empty state
        self._empty_label = QLabel(self)
        self._empty_label.setObjectName("secondaryText")
        self._empty_label.setText(translator.translate(TranslationKey.SECURITY_NO_VULNERABILITIES))
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

    def _on_scan_clicked(self) -> None:
        """Handle scan button click."""
        if self._scanning:
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self) -> None:
        """Start a security scan."""
        self._scanning = True
        self._scan_button.setText(self._translator.translate(TranslationKey.SECURITY_STOP_SCAN))
        self._status_label.setText(self._translator.translate(TranslationKey.SECURITY_SCANNING_IN_PROGRESS))
        self._vuln_list.clear()
        self._empty_label.setVisible(False)
        self._results = None

        # TODO: Implement actual vulnerability scanning
        # For now, show a message
        self._status_label.setText("Security scanning will be implemented in a future update.")

        # Emit scan started signal
        self.scan_started.emit()

    def _stop_scan(self) -> None:
        """Stop the current scan."""
        self._scanning = False
        self._scan_button.setText(self._translator.translate(TranslationKey.SECURITY_START_SCAN))
        self._status_label.clear()

    def set_results(self, result: ScanResult) -> None:
        """Set scan results."""
        self._results = result
        self._vulnerabilities = result.vulnerabilities
        self._scanning = False
        self._scan_button.setText(self._translator.translate(TranslationKey.SECURITY_START_SCAN))
        self._status_label.clear()

        self._vuln_list.clear()
        self._empty_label.setVisible(False)

        if not result.vulnerabilities:
            self._empty_label.setText(self._translator.translate(TranslationKey.SECURITY_NO_VULNERABILITIES))
            self._empty_label.setVisible(True)
        else:
            self._empty_label.setVisible(False)
            for vuln in result.vulnerabilities:
                item = QListWidgetItem()
                severity_text = self._translator.translate(_severity_key(vuln.severity))
                item.setText(f"{vuln.package} ({vuln.version}) - {severity_text}")
                item.setToolTip(
                    f"Package: {vuln.package}\n"
                    f"Version: {vuln.version}\n"
                    f"Severity: {severity_text}\n"
                    f"CVE: {vuln.cve_id or 'N/A'}\n"
                    f"Description: {vuln.description}\n"
                    f"Fixed in: {vuln.fixed_in or 'Not fixed yet'}\n"
                    f"Details: {vuln.details or 'No additional details'}"
                )
                item.setData(Qt.ItemDataRole.UserRole, vuln)
                self._vuln_list.addItem(item)

    def _stop_scan_with_result(self, result: ScanResult) -> None:
        """Stop scan and show results."""
        self._scanning = False
        self._scan_button.setText(self._translator.translate(TranslationKey.SECURITY_START_SCAN))
        self.set_results(result)

    def retranslate(self, translator: Translator) -> None:
        """Re-render strings after language switch."""
        self._translator = translator
        self._title_label.setText(translator.translate(TranslationKey.SECURITY_TITLE))
        self._scan_button.setText(translator.translate(TranslationKey.SECURITY_START_SCAN))
        self._results_label.setText(translator.translate(TranslationKey.SECURITY_RESULTS))
        self._empty_label.setText(translator.translate(TranslationKey.SECURITY_NO_VULNERABILITIES))

        # Update tooltips for existing items
        for i in range(self._vuln_list.count()):
            item = self._vuln_list.item(i)
            vuln = item.data(Qt.ItemDataRole.UserRole)
            if vuln:
                severity_text = self._translator.translate(_severity_key(vuln.severity))
                item.setToolTip(
                    f"Package: {vuln.package}\n"
                    f"Version: {vuln.version}\n"
                    f"Severity: {severity_text}\n"
                    f"CVE: {vuln.cve_id or 'N/A'}\n"
                    f"Description: {vuln.description}\n"
                    f"Fixed in: {vuln.fixed_in or 'Not fixed yet'}\n"
                    f"Details: {vuln.details or 'No additional details'}"
                )
                item.setText(f"{vuln.package} ({vuln.version}) - {severity_text}")

    def refresh(self) -> None:
        """Refresh the security view."""
        self._vuln_list.clear()
        self._vulnerabilities.clear()
        self._results = None
        self._scanning = False
        self._scan_button.setText(self._translator.translate(TranslationKey.SECURITY_START_SCAN))
        self._status_label.clear()
        self._empty_label.setVisible(True)
        self._empty_label.setText(self._translator.translate(TranslationKey.SECURITY_NO_VULNERABILITIES))


def _severity_key(severity: Severity) -> str:
    """Get translation key for severity level."""
    mapping = {
        Severity.LOW: TranslationKey.SECURITY_LOW,
        Severity.MEDIUM: TranslationKey.SECURITY_MEDIUM,
        Severity.HIGH: TranslationKey.SECURITY_HIGH,
        Severity.CRITICAL: TranslationKey.SECURITY_CRITICAL,
        Severity.UNKNOWN: TranslationKey.SECURITY_UNKNOWN,
    }
    return mapping[severity]
