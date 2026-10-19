"""The environments view: discover and switch between Python environments.

SPEC section 3 asks for an environment switcher. This view gives it a dedicated
page so the user can see every environment PyPackHub found, what kind each one is,
and switch to it with one click - instead of only picking from the top-bar combo.

The view never changes an environment itself. Switching is a read-only operation:
it updates the shared ``UiContext`` default, the packages view and the top-bar
combo all at once.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.environments import (
    DEFAULT_SEARCH_ROOTS,
    EnvironmentListing,
    EnvironmentSource,
    ListedEnvironment,
    detected_environments,
)
from pypackhub.core.platform_guard import EnvironmentKind, InterpreterFacts
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.toast import ToastKind
from pypackhub.ui.worker_signals import emit_if_alive

__all__ = ["EnvironmentsView"]


def _kind_label(kind: EnvironmentKind, translator) -> str:
    labels = {
        EnvironmentKind.VENV: "venv",
        EnvironmentKind.CONDA: "conda",
        EnvironmentKind.PYBENV: "pyenv",
        EnvironmentKind.SYSTEM: "system",
    }
    return labels.get(kind, kind.value)


def _source_label(source: EnvironmentSource, translator) -> str:
    labels = {
        EnvironmentSource.SCAN: "found by scan",
        EnvironmentSource.EXPLICIT: "added manually",
        EnvironmentSource.RUNNING: "running interpreter",
    }
    return labels.get(source, source.value)


class EnvironmentsView(QWidget):
    """A page that lists every discovered Python environment."""

    #: Emitted when the user picks an environment to switch to.
    environment_selected = Signal(str)
    #: Emitted with a message and a :class:`~pypackhub.ui.widgets.toast.ToastKind`.
    toast = Signal(str, object)
    #: The listing a discovery worker found, handed over from that thread;
    #: Qt queues it onto the GUI thread because this widget lives there.
    environments_ready = Signal(object)

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
        self._listing: EnvironmentListing | None = None
        self._load_task: str = ""
        self._refreshing = False

        self._build_ui()
        self._connect()
        self.retranslate()
        self._load()

    # -- construction ------------------------------------------------------

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

        self.refresh_button = QPushButton(self)
        self.refresh_button.clicked.connect(self._refresh)
        header.addWidget(self.refresh_button)

        layout.addLayout(header)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("environmentList")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._scroll, 1)

        self._body = QWidget(self._scroll)
        self._scroll.setWidget(self._body)

        self._inner = QVBoxLayout(self._body)
        self._inner.setContentsMargins(8, 8, 8, 8)
        self._inner.setSpacing(8)

        self._cards: dict[str, QWidget] = {}

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

    def _connect(self) -> None:
        self.refresh_button.clicked.connect(self._refresh)
        self.environments_ready.connect(self._on_environments_ready)

    # -- data -------------------------------------------------------------

    def _load(self) -> None:
        """Discover environments on a worker thread, never on the GUI one.

        Discovery walks the standard roots and the working directory - on a
        slow or networked home that takes seconds, and running it here used
        to freeze the window before its first frame, because this method
        runs in the constructor. The listing arrives by signal; until it
        does the page shows its empty state, exactly like the packages table
        before its own listing lands.
        """
        if self._load_task:
            return

        roots: list[Path] = list(DEFAULT_SEARCH_ROOTS)
        roots.append(Path.cwd())

        def work(_task_id: str, _progress: object, _cancel: threading.Event) -> object:
            return detected_environments(tuple(roots))

        self._load_task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.NAV_ENVIRONMENTS),
            work,
            on_finish=lambda _task_id, result: emit_if_alive(self.environments_ready, result),
        )

    def _on_environments_ready(self, result: Any) -> None:
        """Render the listing the worker found; a refresh reports its outcome."""
        self._load_task = ""
        listing = result.payload if isinstance(result.payload, EnvironmentListing) else None
        if listing is not None:
            self._listing = listing
        self._render()

        if not self._refreshing:
            return
        self._refreshing = False
        error = str(getattr(result, "error", "") or "")
        if error:
            self.toast.emit(error, ToastKind.ERROR)
            return
        count = len(self._listing.environments) if self._listing else 0
        self.toast.emit(
            self._shell.text(TranslationKey.DEPS_SCANNED, count=count),
            ToastKind.SUCCESS,
        )

    def _refresh(self) -> None:
        """Reload; the toast follows the answer, not the click."""
        self._refreshing = True
        self._load()

    # -- rendering -------------------------------------------------------

    def _clear_cards(self) -> None:
        """Remove the previously rendered cards from the body.

        The cards are real widgets, not just layout items, so they have to be
        removed and scheduled for deletion. Rebuilding the layout while the old
        cards were still children of the body left the old and new rows stacked
        on top of each other; taking each item out first keeps one set of cards.
        """
        while (item := self._inner.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()

    def _render(self) -> None:
        self._clear_cards()

        listing = self._listing
        if listing is None or not listing.environments:
            self.empty_label.setText(self._shell.text(TranslationKey.PACKAGES_EMPTY))
            self.empty_label.setVisible(True)
            self.summary_label.setText("")
            return

        self.empty_label.setVisible(False)
        count = len(listing.environments)
        self.summary_label.setText(self._shell.text(TranslationKey.PACKAGES_COUNT, count=count))

        current = str(self._context.default_environment)

        for env in listing.environments:
            prefix = str(env.prefix)
            facts = env.facts

            card = self._make_card(env, facts, prefix, prefix == current)
            self._inner.addWidget(card)
            self._cards[prefix] = card

        self._inner.addStretch(1)

    def _make_card(
        self,
        env: ListedEnvironment,
        facts: InterpreterFacts,
        prefix: str,
        is_current: bool,
    ) -> QWidget:
        card = QWidget(self._body)
        card.setObjectName("contentPanel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)

        switch_button = QPushButton(self)
        switch_button.setObjectName("primaryButton")
        switch_button.setText(self._shell.text(TranslationKey.NAV_ENVIRONMENTS))
        switch_button.setCheckable(True)
        switch_button.setChecked(is_current)
        switch_button.setAutoDefault(False)
        top.addWidget(switch_button)

        top.addStretch(1)

        self._attach_metadata(top, facts, env.source)

        layout.addLayout(top)

        path_label = QLabel(prefix, card)
        path_label.setObjectName("versionLabel")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        detail = QLabel(card)
        detail.setObjectName("secondaryText")
        detail.setWordWrap(True)
        detail.setText(self._detail_text(facts, env.source))
        layout.addWidget(detail)

        if is_current:
            card.setStyleSheet(
                "QWidget#contentPanel { border: 1px solid "
                + self._shell.active_theme.tokens.accent_primary
                + "; border-radius: 6px; }"
            )

        switch_button.clicked.connect(lambda checked=False, p=prefix: self._on_switch(p))

        return card

    def _attach_metadata(
        self, layout: QHBoxLayout, facts: InterpreterFacts, source: EnvironmentSource
    ) -> None:
        backend_text = self._shell.text(TranslationKey.PACKAGES_BACKEND)
        kind_text = backend_text + ": " + _kind_label(facts.kind, self._shell.translator)
        kind_label = QLabel(kind_text, self)
        kind_label.setObjectName("mutedText")
        layout.addWidget(kind_label)

        if facts.externally_managed:
            badge = QLabel(self)
            badge.setText(self._shell.text(TranslationKey.STATUS_OUTDATED))
            badge.setObjectName("errorText")
            layout.addWidget(badge)

    def _detail_text(self, facts: InterpreterFacts, source: EnvironmentSource) -> str:
        parts = []
        if facts.executable is not None:
            parts.append("python: " + str(facts.executable))
        parts.append(_source_label(source, self._shell.translator))
        if facts.kind is EnvironmentKind.SYSTEM:
            parts.append("base interpreter")
        if facts.externally_managed:
            parts.append("externally managed (PEP 668)")
        return "  ·  ".join(parts)

    def _on_switch(self, prefix: str) -> None:
        prefix_path = Path(prefix)
        service = self._context.provider.create(prefix_path)
        self._context.set_default_environment(service.environment)
        self.environment_selected.emit(prefix)
        self._render()

    # -- re-translation --------------------------------------------------

    def retranslate(self) -> None:
        self.title_label.setText(self._shell.text(TranslationKey.NAV_ENVIRONMENTS))
        self.refresh_button.setText(self._shell.text(TranslationKey.DEPS_REFRESH))
        self._render()
