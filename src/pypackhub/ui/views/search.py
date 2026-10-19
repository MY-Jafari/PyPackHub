"""The search view: find a package on PyPI and install it into the environment.

PyPI has no search API any more, so searching means choosing which names to
*look up*: the bundled popular-package catalogue is ranked against the query
locally and instantly, the query itself is always looked up as an exact name,
and the winners are resolved against the PyPI JSON API - a few at a time,
with the list filling in as answers arrive.

Selecting a result loads its release history, so a version can be picked and
pinned rather than blindly taking "latest". Installing never happens here:
the view emits the same validated action the rest of the application uses,
and the window routes it to the packages view's confirm-and-run path - one
place shows the exact command, one place asks for the user's approval.
"""

from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import ActionKind, PackageAction
from pypackhub.core.pypi import PyPIClient
from pypackhub.core.search import SearchService
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.spinner import Spinner, SpinnerSize
from pypackhub.ui.widgets.toast import ToastKind
from pypackhub.ui.worker_signals import emit_if_alive

__all__ = ["SearchView"]

#: How many of a package's versions the picker offers. The history of a
#: mature package is long, and the interesting end of it is the newest.
VERSION_PICKER_LIMIT = 25


class SearchResultItem(QListWidgetItem):
    """One resolved result, carrying everything the install path needs."""

    def __init__(self, result: Any) -> None:
        super().__init__()
        self.result = result
        version = result.latest_version or "—"
        self.setText(f"{result.name} ({version})")
        summary = result.summary or ""
        if result.error:
            summary = f"{summary}\n{result.error}".strip()
        self.setToolTip(f"{result.name}\n{summary}")


class SearchView(QWidget):
    """Search PyPI, inspect a package's versions, and install a chosen one."""

    #: A validated install action for the current environment. The window
    #: routes it to the packages view's confirm-and-run path.
    install_requested = Signal(object)
    status_changed = Signal(str)
    toast = Signal(str, object)

    #: From worker threads; Qt queues them onto the GUI thread.
    search_finished = Signal(object)
    search_result = Signal(object)
    meta_loaded = Signal(str, object)

    def __init__(
        self,
        *,
        context: UiContext,
        shell: WindowShell,
        search_service: SearchService | None = None,
        client: PyPIClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._shell = shell
        self._service = search_service
        self._client = client
        self._task = ""
        self._meta_task = ""
        self._selected: Any = None
        self._summary = ""

        self.setObjectName("searchPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("pageTitle")
        layout.addWidget(self.title_label)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.query_edit = QLineEdit(self)
        self.query_edit.setObjectName("searchInput")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.returnPressed.connect(self.search)
        search_row.addWidget(self.query_edit, 1)

        self.search_button = QPushButton(self)
        self.search_button.setObjectName("primaryButton")
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.spinner = Spinner(size=SpinnerSize.SMALL)
        self.spinner.hide()
        self.status_label = QLabel(self)
        self.status_label.setObjectName("secondaryText")
        self.status_label.setWordWrap(True)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.spinner)
        layout.addLayout(status_row)

        self.results_list = QListWidget(self)
        self.results_list.setObjectName("searchResultsList")
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.results_list, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setObjectName("emptyStateBody")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        # -- the selected package ------------------------------------
        self.detail_label = QLabel(self)
        self.detail_label.setObjectName("sectionHeading")
        layout.addWidget(self.detail_label)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(8)

        self.version_combo = QComboBox(self)
        self.version_combo.setMinimumWidth(160)
        detail_row.addWidget(self.version_combo, 1)

        self.install_button = QPushButton(self)
        self.install_button.setObjectName("primaryButton")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install_selected)
        detail_row.addWidget(self.install_button)
        layout.addLayout(detail_row)

        self.retranslate()
        self.search_finished.connect(self._on_search_finished)
        self.search_result.connect(self._on_search_result)
        self.meta_loaded.connect(self._on_meta_loaded)

    # -- searching -----------------------------------------------------

    @property
    def service(self) -> SearchService:
        if self._service is None:
            self._service = SearchService(client=self._make_client())
        return self._service

    def _make_client(self) -> PyPIClient:
        """A client pointed at the index the settings describe.

        The settings page's index URL was validated before it was stored;
        the search answers come from the same index the upgrades install
        from, or the two halves of the app disagree about the world.
        """
        settings = self._shell.settings
        return PyPIClient(
            index_url=settings.pypi_index_url,
            ttl=settings.pypi_cache_ttl,
            max_versions=settings.pypi_cache_max_versions,
        )

    def search(self) -> None:
        """Run the query on a worker thread, filling the list as answers land."""
        query = self.query_edit.text().strip()
        if not query or self._task:
            return

        self.results_list.clear()
        self._selected = None
        self._summary = ""
        self._update_detail()
        self.empty_label.setVisible(False)
        self.status_changed.emit(self._shell.text(TranslationKey.SEARCH_LOADING))
        self.spinner.start()
        self.spinner.show()
        self.search_button.setEnabled(False)

        service = self.service

        def work(_task_id: str, _progress: object, cancel: threading.Event) -> list[Any]:
            return service.search(
                query, cancel=cancel, on_result=lambda row: emit_if_alive(self.search_result, row)
            )

        self._task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.SEARCH_TITLE),
            work,
            on_finish=lambda _task_id, result: emit_if_alive(self.search_finished, result),
        )

    def _on_search_result(self, result: Any) -> None:
        """One answer arrived; show it immediately."""
        self.results_list.addItem(SearchResultItem(result))

    def _on_search_finished(self, result: Any) -> None:
        self._task = ""
        self.spinner.stop()
        self.spinner.hide()
        self.search_button.setEnabled(True)

        outcome = getattr(result, "payload", None)
        error = getattr(result, "error", "") or ""

        rows = outcome if isinstance(outcome, list) else []
        if not rows and error:
            message = f"{self._shell.text(TranslationKey.SEARCH_ERROR)}: {error}"
            self.status_changed.emit(message)
            self.toast.emit(message, ToastKind.ERROR)
            return

        if not rows:
            self.empty_label.setText(self._shell.text(TranslationKey.SEARCH_NO_RESULTS))
            self.empty_label.setVisible(True)
            self.status_changed.emit("")
            return

        self.status_changed.emit(f"{self._shell.text(TranslationKey.SEARCH_RESULTS)}: {len(rows)}")

    # -- the selected package ------------------------------------------

    def _on_selection_changed(self) -> None:
        selected = self.results_list.selectedItems()
        item = selected[0] if selected else None
        if not isinstance(item, SearchResultItem):
            self._selected = None
            self._update_detail()
            return

        self._selected = item.result
        self._update_detail()
        self._load_versions(str(self._selected.name))

    def _load_versions(self, name: str) -> None:
        """Fetch the package's release history for the version picker."""
        if self._meta_task:
            return

        client = self._client if self._client is not None else self._make_client()

        def work(_task_id: str, _progress: object, _cancel: threading.Event) -> object:
            return client.meta(name)

        def finished(_task_id: str, result: object) -> None:
            # Worker thread: compute what the payload is, then hand it over
            # with the guarded emit; the busy-flag belongs to the GUI thread.
            payload = getattr(result, "payload", None)
            error = getattr(result, "error", "")
            emit_if_alive(
                self.meta_loaded,
                name,
                payload if error == "" else type("Meta", (), {"versions": ()})(),
            )

        self._meta_task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.SEARCH_LOADING),
            work,
            on_finish=finished,
        )

    def _on_meta_loaded(self, name: str, meta: object) -> None:
        self._meta_task = ""
        if self._selected is None or str(self._selected.name) != name:
            return

        info = getattr(meta, "info", None)
        versions = tuple(getattr(meta, "versions", ()) or ())
        self._summary = str(getattr(info, "summary", "") or "") if info is not None else ""

        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        # The first entry means "latest, unpinned" - the version the Latest
        # column has been showing all along; the rest are the package's own
        # published history, newest first, so a pin is an explicit choice.
        self.version_combo.addItem(self._shell.text(TranslationKey.SEARCH_INSTALL_LATEST), "")
        for version in versions[:VERSION_PICKER_LIMIT]:
            self.version_combo.addItem(version, version)
        self.version_combo.blockSignals(False)

        self.install_button.setEnabled(True)
        self._update_detail()

    def _install_selected(self) -> None:
        """Emit the validated install action for the picked version."""
        if self._selected is None:
            return

        name = str(self._selected.name)
        version = str(self.version_combo.currentData() or "")
        spec = f"{name}=={version}" if version else name

        self.install_requested.emit(
            PackageAction(
                kind=ActionKind.INSTALL,
                environment=self._context.default_environment,
                specs=(spec,),
            )
        )

    def _update_detail(self) -> None:
        if self._selected is None:
            self.detail_label.setText("")
            self.install_button.setEnabled(False)
            return

        title = str(self._selected.name)
        if self._summary:
            title = f"{title} — {self._summary}"
        self.detail_label.setText(title)
        self.detail_label.setToolTip(self._summary)
        self.install_button.setEnabled(bool(self.version_combo.count()))

    # -- chrome --------------------------------------------------------

    def retranslate(self) -> None:
        shell = self._shell
        self.title_label.setText(shell.text(TranslationKey.SEARCH_TITLE))
        self.query_edit.setPlaceholderText(shell.text(TranslationKey.SEARCH_PLACEHOLDER))
        self.search_button.setText(shell.text(TranslationKey.SEARCH_QUERY))
        self.empty_label.setText(shell.text(TranslationKey.SEARCH_NO_RESULTS))
        self.install_button.setText(shell.text(TranslationKey.SEARCH_INSTALL))
        if self.version_combo.count():
            self.version_combo.setItemText(0, shell.text(TranslationKey.SEARCH_INSTALL_LATEST))
        if self._selected is None:
            self.detail_label.setText("")
        else:
            self.detail_label.setText(str(self._selected.name))

    def refresh(self) -> None:
        """Kept for navigation symmetry; a search page keeps its results."""
