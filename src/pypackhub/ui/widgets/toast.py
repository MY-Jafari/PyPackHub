"""Toast notifications.

SPEC section 7 asks for toasts, and they are the right way to report the end of a
long action: a modal dialog for "installed flask" interrupts whatever the user
moved on to. A toast appears over the window, is announced to accessibility
tools, and disappears on its own.

The host is a transparent child of the window it decorates. It re-lays its
messages out on every resize, so a toast never ends up under the window edge.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pypackhub.ui.theme.design_tokens import AccentRole, DesignTokens

__all__ = ["ToastHost", "ToastKind"]

#: How long a message stays before it fades out. Long enough to read a sentence,
#: short enough not to become furniture.
DEFAULT_TIMEOUT_MS: Final = 6000


class ToastKind(str, Enum):
    """What a message is reporting, which decides its colour."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


_KIND_ROLES: Final[dict[ToastKind, AccentRole]] = {
    ToastKind.INFO: AccentRole.ORPHAN,
    ToastKind.SUCCESS: AccentRole.UP_TO_DATE,
    ToastKind.WARNING: AccentRole.OUTDATED,
    ToastKind.ERROR: AccentRole.VULNERABLE,
}


class _Toast(QLabel):
    """One message, styled from the design tokens."""

    dismissed = Signal(object)

    def __init__(
        self,
        text: str,
        kind: ToastKind,
        *,
        tokens: DesignTokens,
        timeout_ms: int,
        parent: QWidget,
    ) -> None:
        super().__init__(text, parent)
        self._kind = kind
        self.setObjectName("toast")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # A toast is a status message, not a button: it must not swallow clicks
        # meant for the window underneath it.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.apply_tokens(tokens)
        self.setAccessibleName(text)

        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, self._dismiss)

    @property
    def kind(self) -> ToastKind:
        return self._kind

    def apply_tokens(self, tokens: DesignTokens) -> None:
        colour = tokens.color_for_role(_KIND_ROLES[self._kind])
        self.setStyleSheet(
            f"#toast {{"
            f" background-color: {tokens.background_elevated};"
            f" border: 1px solid {colour};"
            f" border-left: 3px solid {colour};"
            f" border-radius: {tokens.radius_m};"
            f" color: {tokens.text_primary};"
            f" padding: 10px 14px;"
            f"}}"
        )

    def _dismiss(self) -> None:
        self.dismissed.emit(self)


class ToastHost(QWidget):
    """Stacks toast messages in the bottom-right corner of a window."""

    message_shown = Signal(str, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toastHost")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 16, 16)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignTrailing)

        self._messages: list[_Toast] = []
        self._tokens: DesignTokens | None = None
        self._timeout_ms = DEFAULT_TIMEOUT_MS

        parent.installEventFilter(self)
        self.resize_to_parent()

    # -- messages ------------------------------------------------------

    @property
    def messages(self) -> tuple[_Toast, ...]:
        return tuple(self._messages)

    @property
    def active_count(self) -> int:
        return len(self._messages)

    def show_message(
        self,
        text: str,
        kind: ToastKind = ToastKind.INFO,
        *,
        timeout_ms: int | None = None,
    ) -> _Toast:
        """Show a message and return the widget, for tests and for callers."""
        if self._tokens is None:
            from pypackhub.ui.theme.design_tokens import default_light_tokens

            self._tokens = default_light_tokens()

        toast = _Toast(
            text,
            kind,
            tokens=self._tokens,
            timeout_ms=self._timeout_ms if timeout_ms is None else timeout_ms,
            parent=self,
        )
        toast.dismissed.connect(self._remove)
        self._layout.addWidget(toast)
        self._messages.append(toast)
        self.raise_()
        self.message_shown.emit(text, kind)
        return toast

    def clear(self) -> None:
        for toast in list(self._messages):
            self._remove(toast)

    def apply_tokens(self, tokens: DesignTokens) -> None:
        """Re-colour every visible message after a theme switch."""
        self._tokens = tokens
        for toast in self._messages:
            toast.apply_tokens(tokens)

    # -- geometry ------------------------------------------------------

    def resize_to_parent(self) -> None:
        """Cover the parent window so toasts anchor to its bottom-right corner."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self.resize_to_parent()
        return False

    # -- internal ------------------------------------------------------

    def _remove(self, toast: _Toast) -> None:
        if toast in self._messages:
            self._messages.remove(toast)
        self._layout.removeWidget(toast)
        toast.setParent(None)
        toast.deleteLater()
