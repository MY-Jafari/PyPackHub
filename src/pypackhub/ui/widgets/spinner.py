"""A small circular spinner that animates while something is loading.

It is pure PySide6 and paints itself with a rotating arc, so it has no
external asset dependency and works headless in tests (when hidden or at
zero size).
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

__all__ = ["Spinner", "SpinnerSize"]


class SpinnerSize(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Spinner(QWidget):
    """An animated circular spinner.

    When ``start()`` is called the arc begins rotating; ``stop()`` freezes it
    and hides the widget. The widget is invisible while stopped.
    """

    TIMER_INTERVAL_MS = 30

    def __init__(
        self,
        *,
        size: SpinnerSize = SpinnerSize.MEDIUM,
        color: str = "#3b82f6",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("spinner")

        self._size_table = {
            SpinnerSize.SMALL: 16,
            SpinnerSize.MEDIUM: 28,
            SpinnerSize.LARGE: 44,
        }
        self._diameter = self._size_table[size]
        self._color = color
        self._angle = 0.0
        self._running = False

        self.setFixedSize(self._diameter, self._diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TIMER_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        self.setVisible(False)

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._timer.start()
        self.setVisible(True)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self.setVisible(False)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._diameter = min(self.width(), self.height())
        super().resizeEvent(event)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen()
        pen.setColor(self._color)
        pen.setWidth(max(2, self._diameter // 8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        painter.translate(self._diameter / 2, self._diameter / 2)
        painter.rotate(self._angle)

        radius = self._diameter / 2 - pen.width() / 2 - 1
        start_angle = -90 * 16
        span_angle = 270 * 16

        painter.drawArc(
            int(-radius),
            int(-radius),
            int(radius * 2),
            int(radius * 2),
            start_angle,
            span_angle,
        )
        painter.end()

    # -- private -----------------------------------------------------------

    def _tick(self) -> None:
        self._angle = (self._angle + 6) % 360
        self.update()
