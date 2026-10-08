"""The live terminal panel.

Every changing action streams its output here as it happens, so the user sees
progress instead of a frozen window, and can cancel the whole process tree from
the same place (SPEC sections 5 and 6). The panel is also the durable record: it
keeps what it has, and can be written to a file.

The panel formats nothing about commands itself. It is handed lines by whoever
runs the task, so there is one place that renders a command - the plan that was
confirmed.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Final

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator

__all__ = ["LogPanel", "RunOutcome"]

#: How many lines the panel keeps. A pip install can produce thousands of lines;
#: the file this application writes to disk is the full record.
MAX_LINES: Final = 5000


class RunOutcome(str, Enum):
    """How a run ended, which is what the panel reports."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LogPanel(QWidget):
    """A read-only console, with a Cancel button while something is running."""

    cancel_requested = Signal()

    def __init__(self, *, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._translator = translator
        self._state = RunOutcome.DONE

        self.setObjectName("logPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)

        self._title = QLabel(self)
        self._title.setObjectName("logHeader")
        header.addWidget(self._title)

        self._status = QLabel(self)
        self._status.setObjectName("logHeader")
        header.addWidget(self._status)
        header.addStretch(1)

        self._clear_button = QPushButton(self)
        self._clear_button.setObjectName("ghostButton")
        self._clear_button.clicked.connect(self.clear)
        header.addWidget(self._clear_button)

        self._save_button = QPushButton(self)
        self._save_button.setObjectName("ghostButton")
        self._save_button.clicked.connect(self.save_to_file)
        header.addWidget(self._save_button)

        self._cancel_button = QPushButton(self)
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.clicked.connect(self.cancel_requested.emit)
        self._cancel_button.setEnabled(False)
        header.addWidget(self._cancel_button)

        layout.addLayout(header)

        self._console = QPlainTextEdit(self)
        self._console.setObjectName("logConsole")
        self._console.setReadOnly(True)
        self._console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._console.setMaximumBlockCount(MAX_LINES)
        self._console.setMinimumHeight(120)
        layout.addWidget(self._console, 1)

        self.set_state(RunOutcome.DONE)
        self.retranslate(translator)

    # -- content -------------------------------------------------------

    def append_line(self, text: str) -> None:
        """Append one line of output."""
        self._console.appendPlainText(text.rstrip("\r\n"))

    def append_lines(self, lines: Iterable[str]) -> None:
        """Append several lines at once, for output that arrived as a block."""
        for line in lines:
            self.append_line(line)

    def begin_run(self, label: str) -> None:
        """Mark the start of a run: the task label, then its command."""
        self._console.appendPlainText("")
        self._console.appendPlainText(f"=== {label} ===")
        self.set_state(RunOutcome.RUNNING)

    def end_run(self, outcome: RunOutcome, *, error: str = "") -> None:
        """Mark the end of a run with its translated outcome line."""
        self.set_state(outcome)
        self.append_line(self._outcome_text(outcome, error=error))

    def clear(self) -> None:
        self._console.clear()

    def text(self) -> str:
        return self._console.toPlainText()

    def save_to_file(self, path: str | Path | None = None) -> str:
        """Write the panel's contents to *path*, asking for one when needed.

        Returns the path that was written, or an empty string when the user
        cancelled the file dialog.
        """
        target = path
        if target is None:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                self._translator.translate(TranslationKey.LOG_SAVE),
                str(Path.home() / "pypackhub-log.txt"),
            )
            if not chosen:
                return ""
            target = chosen

        Path(target).write_text(self.text(), encoding="utf-8")
        return str(target)

    # -- state ---------------------------------------------------------

    @property
    def state(self) -> RunOutcome:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is RunOutcome.RUNNING

    def set_state(self, outcome: RunOutcome) -> None:
        """Update the state indicator and the Cancel button's availability."""
        self._state = outcome
        running = outcome is RunOutcome.RUNNING
        self._cancel_button.setEnabled(running)
        self._status.setText(self._state_text(outcome))

    # -- translation ---------------------------------------------------

    def retranslate(self, translator: Translator) -> None:
        self._translator = translator
        self._title.setText(translator.translate(TranslationKey.LOG_TITLE))
        self._clear_button.setText(translator.translate(TranslationKey.LOG_CLEAR))
        self._save_button.setText(translator.translate(TranslationKey.LOG_SAVE))
        self._cancel_button.setText(translator.translate(TranslationKey.BUTTON_CANCEL))
        self._cancel_button.setToolTip(translator.translate(TranslationKey.LOG_CANCEL))
        self.set_state(self._state)

    def _state_text(self, outcome: RunOutcome) -> str:
        """The short status word in the header, without the error detail."""
        if outcome is RunOutcome.RUNNING:
            return self._translator.translate(TranslationKey.LOG_RUNNING)
        if outcome is RunOutcome.CANCELLED:
            return self._translator.translate(TranslationKey.LOG_CANCELLED)
        if outcome is RunOutcome.FAILED:
            return self._translator.translate(TranslationKey.TOAST_FAILED)
        return self._translator.translate(TranslationKey.LOG_DONE)

    def _outcome_text(self, outcome: RunOutcome, *, error: str) -> str:
        """The line appended to the console when a run ends, with its error."""
        if outcome is RunOutcome.DONE:
            return self._translator.translate(TranslationKey.LOG_DONE)
        if outcome is RunOutcome.CANCELLED:
            return self._translator.translate(TranslationKey.LOG_CANCELLED)
        if outcome is RunOutcome.FAILED:
            return self._translator.translate(TranslationKey.LOG_FAILED, error=error)
        return self._translator.translate(TranslationKey.LOG_RUNNING)
