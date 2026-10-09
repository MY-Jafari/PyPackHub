"""The dependencies view: the graph, read top to bottom.

Three questions, in the order a user actually asks them:

1. **Why is this here?** Selecting a package shows the chain from the package the
   user asked for down to this one, or says plainly that nothing needs it.
2. **What does it need, and what needs it?** Both directions, as trees, with the
   path a package was reached by rather than a flat list.
3. **What can go?** The orphans - required by nothing and installed by nobody -
   with a dry run before anything is removed.

The view never changes an environment itself. The preview ends at a
:class:`~pypackhub.core.backends.base.PackageAction`, which is emitted for the
window to hand to the packages view's confirm-and-run path: one place shows the
exact command, one place gets the user's approval, and this page cannot become a
second, quieter way to uninstall things.

Reading the metadata is the expensive part, so it runs on a ``core.tasks`` worker
and the result arrives as a Qt signal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import PackageAction
from pypackhub.core.deps import DependencyGraph, Direction, build_graph
from pypackhub.core.metadata import InstalledMetadataIndex, normalize_name
from pypackhub.core.tasks import TaskResult
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.dialogs import RemovalPreviewDialog

__all__ = ["DependenciesView"]


class DependenciesView(QWidget):
    """The installed packages, their relationships, and the unneeded ones."""

    #: Emitted from a worker thread; Qt queues them onto the GUI thread.
    graph_ready = Signal(object)
    status_changed = Signal(str)

    #: A removal the user previewed and asked to proceed with. The window routes
    #: it to the packages view so the confirmation and the log panel are shared.
    removal_requested = Signal(object)

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

        self._graph: DependencyGraph | None = None
        self._index: InstalledMetadataIndex | None = None
        self._scan_task: str = ""
        self._selected_key: str = ""
        # name -> measured bytes, offered by the packages view when it has already
        # measured them; empty means the orphan sizes are simply unknown.
        self._size_source: dict[str, int | None] = {}

        self._groups: dict[TranslationKey, QGroupBox] = {}

        self._build_ui()
        self._connect()
        self.retranslate()

    # -- construction -----------------------------------------------------------

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

        self.rescan_button = QPushButton(self)
        self.rescan_button.clicked.connect(lambda: self.refresh(force=True))
        header.addWidget(self.rescan_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # -- left: every installed package ------------------------------------
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.search_edit = QLineEdit(left)
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setClearButtonEnabled(True)
        left_layout.addWidget(self.search_edit)

        self.package_list = QTreeWidget(left)
        self.package_list.setObjectName("dependencyTree")
        self.package_list.setHeaderHidden(True)
        self.package_list.setRootIsDecorated(False)
        self.package_list.setUniformRowHeights(True)
        self.package_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        left_layout.addWidget(self.package_list, 1)

        # -- right: the selected package, read three ways ----------------------
        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.prompt_label = QLabel(right)
        self.prompt_label.setObjectName("secondaryText")
        self.prompt_label.setWordWrap(True)
        right_layout.addWidget(self.prompt_label)

        reason_group, reason_layout = self._group(right_layout, TranslationKey.DEPS_WHY)
        self.reason_label = QLabel(reason_group)
        self.reason_label.setObjectName("secondaryText")
        self.reason_label.setWordWrap(True)
        reason_layout.addWidget(self.reason_label)

        requires_group, requires_layout = self._group(right_layout, TranslationKey.DEPS_REQUIRES)
        self.requires_tree = self._tree(requires_group)
        requires_layout.addWidget(self.requires_tree)

        required_by_group, required_by_layout = self._group(
            right_layout, TranslationKey.DEPS_REQUIRED_BY
        )
        self.required_by_tree = self._tree(required_by_group)
        required_by_layout.addWidget(self.required_by_tree)

        self.missing_group, missing_layout = self._group(right_layout, TranslationKey.DEPS_MISSING)
        self.missing_label = QLabel(self.missing_group)
        self.missing_label.setObjectName("errorText")
        self.missing_label.setWordWrap(True)
        missing_layout.addWidget(self.missing_label)

        right_layout.addStretch(1)
        layout.addWidget(splitter, 1)

        # -- bottom: what can go ----------------------------------------------
        orphans_group, orphans_layout = self._group(layout, TranslationKey.DEPS_ORPHANS)
        self.orphans_note = QLabel(orphans_group)
        self.orphans_note.setObjectName("secondaryText")
        self.orphans_note.setWordWrap(True)
        orphans_layout.addWidget(self.orphans_note)

        self.orphans_list = QListWidget(orphans_group)
        self.orphans_list.setObjectName("orphanList")
        self.orphans_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.orphans_list.setMaximumHeight(110)
        orphans_layout.addWidget(self.orphans_list)

        self.orphans_requested_label = QLabel(orphans_group)
        self.orphans_requested_label.setObjectName("secondaryText")
        self.orphans_requested_label.setWordWrap(True)
        orphans_layout.addWidget(self.orphans_requested_label)

        self.orphans_size_label = QLabel(orphans_group)
        self.orphans_size_label.setObjectName("secondaryText")
        self.orphans_size_label.setWordWrap(True)
        orphans_layout.addWidget(self.orphans_size_label)

        self.clean_up_button = QPushButton(orphans_group)
        self.clean_up_button.setObjectName("dangerButton")
        self.clean_up_button.clicked.connect(self.preview_orphan_removal)
        orphans_layout.addWidget(self.clean_up_button)

        self._update_details(None)
        self._update_orphans(None)

    def _group(
        self, layout: QVBoxLayout | QHBoxLayout, key: TranslationKey
    ) -> tuple[QGroupBox, QVBoxLayout]:
        """Add a titled group box and return it with its inner layout."""
        box = QGroupBox(self)
        box.setObjectName("dependencyGroup")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(10, 16, 10, 10)
        inner.setSpacing(6)
        layout.addWidget(box)
        self._groups[key] = box
        return box, inner

    def _tree(self, parent: QWidget) -> QTreeWidget:
        tree = QTreeWidget(parent)
        tree.setObjectName("dependencyTree")
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tree.setUniformRowHeights(True)
        return tree

    def _connect(self) -> None:
        self.graph_ready.connect(self._on_graph_ready)
        self.package_list.currentItemChanged.connect(self._on_package_selected)
        self.search_edit.textChanged.connect(self._apply_filter)

    # -- loading ---------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> None:
        """Read the environment's metadata on a worker thread and graph it."""
        if self._scan_task and not force:
            return

        environment = self._context.default_environment
        self.status_changed.emit(self._shell.text(TranslationKey.DEPS_SCANNING))

        def work(_task_id: str, _progress: object, _cancel: object) -> object:
            service = self._context.service_for(environment)
            index = service.metadata_index(refresh=force)
            # The inventory names are what the package manager believes is
            # installed; passing them in is what keeps a package with unreadable
            # metadata from disappearing out of the graph entirely.
            inventory = service.list()
            names = tuple(row.name for row in inventory.rows)
            return build_graph(index, installed_names=names)

        self._scan_task = self._context.tasks.schedule(
            self._shell.text(TranslationKey.DEPS_TITLE),
            work,
            on_finish=lambda _task_id, result: self.graph_ready.emit(result),
        )

    def _on_graph_ready(self, result: TaskResult) -> None:
        self._scan_task = ""
        graph = result.payload if isinstance(result.payload, DependencyGraph) else None

        if graph is None:
            message = self._shell.text(TranslationKey.DEPS_FAILED, error=result.error or "unknown")
            self.status_changed.emit(message)
            return

        self.show_graph(graph)
        self.status_changed.emit(self._shell.text(TranslationKey.DEPS_SCANNED, count=len(graph)))

    def show_graph(self, graph: DependencyGraph) -> None:
        """Render an already-built graph.

        The seam between the worker thread and the page, and the one a test can
        drive without a task queue or a subprocess behind it.
        """
        self._graph = graph
        self._populate_packages(graph)
        self._update_orphans(graph)
        self._update_details(self._selected_key or None)

    def _populate_packages(self, graph: DependencyGraph) -> None:
        """Rebuild the package list, keeping the selection when it still exists."""
        self.package_list.blockSignals(True)
        self.package_list.clear()

        for key in sorted(graph.nodes):
            node = graph.nodes[key]
            item = QTreeWidgetItem(self.package_list)
            item.setText(0, self._package_text(node))
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            if node.unknown:
                item.setToolTip(0, self._shell.text(TranslationKey.DEPS_UNKNOWN))
            if key == self._selected_key:
                item.setSelected(True)

        self.package_list.blockSignals(False)
        self._apply_filter(self.search_edit.text())

        selected = self._item_for_key(self._selected_key)
        if selected is not None:
            self.package_list.setCurrentItem(selected)

    def _package_text(self, node: object) -> str:
        name = getattr(node, "name", "")
        version = getattr(node, "version", "")
        unknown = bool(getattr(node, "unknown", False))
        text = f"{name} {version}".strip()
        if unknown:
            text = f"{text} ({self._shell.text(TranslationKey.DEPS_UNKNOWN)})"
        return text

    def _item_for_key(self, key: str) -> QTreeWidgetItem | None:
        if not key:
            return None
        for index in range(self.package_list.topLevelItemCount()):
            item = self.package_list.topLevelItem(index)
            if item is not None and item.data(0, Qt.ItemDataRole.UserRole) == key:
                return item
        return None

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.package_list.topLevelItemCount()):
            item = self.package_list.topLevelItem(index)
            if item is None:
                continue
            item.setHidden(bool(needle) and needle not in item.text(0).lower())

    # -- selection -------------------------------------------------------------

    def _on_package_selected(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        key = current.data(0, Qt.ItemDataRole.UserRole)
        self._selected_key = key if isinstance(key, str) else ""
        self._update_details(self._selected_key)

    def select_package(self, name: str) -> bool:
        """Select a package by name, for keyboard-driven and test flows."""
        item = self._item_for_key(normalize_name(name))
        if item is None:
            return False
        self.package_list.setCurrentItem(item)
        return True

    def _update_details(self, key: str | None) -> None:
        graph = self._graph
        node = graph.get(key) if graph is not None and key else None

        self.prompt_label.setVisible(node is None)
        for group_key in (
            TranslationKey.DEPS_WHY,
            TranslationKey.DEPS_REQUIRES,
            TranslationKey.DEPS_REQUIRED_BY,
        ):
            self._groups[group_key].setVisible(node is not None)

        if node is None or graph is None:
            self.reason_label.clear()
            self.requires_tree.clear()
            self.required_by_tree.clear()
            self.missing_group.setVisible(False)
            return

        self.reason_label.setText(self._reason_text(graph, node.key))
        self._fill_tree(self.requires_tree, graph, node.key, direction="out")
        self._fill_tree(self.required_by_tree, graph, node.key, direction="in")

        missing = graph.missing(node.key)
        self.missing_group.setVisible(bool(missing))
        self.missing_label.setText(", ".join(missing))

    def _reason_text(self, graph: DependencyGraph, key: str) -> str:
        chain = graph.why_installed(key)
        if not chain:
            return self._shell.text(TranslationKey.DEPS_WHY_ORPHAN)
        if len(chain) == 1:
            return self._shell.text(TranslationKey.DEPS_WHY_REQUESTED)
        return " \u2192 ".join(chain)

    def _fill_tree(
        self, tree: QTreeWidget, graph: DependencyGraph, key: str, *, direction: Direction
    ) -> None:
        """Render the subgraph under *key* as a tree, cycle-safe.

        A package reachable by two paths appears under both, because that *is* the
        shape of the graph. A package that would recurse into one of its own
        ancestors is shown and not expanded, so a cyclic environment renders as a
        finite tree with one marked edge instead of hanging the UI thread.
        """
        tree.clear()
        subgraph = graph.subgraph(key, direction=direction)
        if not subgraph:
            self._add_placeholder(tree, self._shell.text(TranslationKey.DEPS_NONE))
            return

        def add(parent: QTreeWidgetItem | QTreeWidget, current: str, path: tuple[str, ...]) -> None:
            item = QTreeWidgetItem(parent)
            item.setText(0, graph.name_of(current))
            if current in path:
                item.setText(
                    0,
                    f"{graph.name_of(current)} " f"({self._shell.text(TranslationKey.DEPS_NONE)})",
                )
                return
            children = [child for child in subgraph.get(current, ()) if child != current]
            if not children and current != key:
                self._add_placeholder(item, self._shell.text(TranslationKey.DEPS_NONE))
            for child in children:
                add(item, child, (*path, current))

        add(tree, key, ())
        root = tree.topLevelItem(0)
        if root is not None and root.childCount() == 0:
            self._add_placeholder(root, self._shell.text(TranslationKey.DEPS_NONE))
        tree.expandToDepth(2)

    def _add_placeholder(self, parent: QTreeWidgetItem | QTreeWidget, text: str) -> None:
        item = QTreeWidgetItem(parent)
        item.setText(0, text)

    # -- orphans ---------------------------------------------------------------

    def _update_orphans(self, graph: DependencyGraph | None) -> None:
        self.clean_up_button.setEnabled(bool(graph and graph.orphans().orphans))
        self.orphans_list.clear()
        self.orphans_note.setText(self._shell.text(TranslationKey.DEPS_ORPHANS_NOTE))

        if graph is None:
            self.orphans_note.setVisible(False)
            self.orphans_requested_label.setVisible(False)
            self.orphans_size_label.setVisible(False)
            self.summary_label.clear()
            return

        report = graph.orphans(sizes=self._measured_sizes(graph))
        self.orphans_note.setVisible(True)

        for name in report.orphans:
            QListWidgetItem(name, self.orphans_list)

        if not report.orphans:
            self.orphans_list.setVisible(False)
            self.orphans_note.setText(self._shell.text(TranslationKey.DEPS_ORPHANS_NONE))
        else:
            self.orphans_list.setVisible(True)

        if report.requested_only:
            self.orphans_requested_label.setText(
                self._shell.text(
                    TranslationKey.DEPS_ORPHANS_REQUESTED_ONLY,
                    count=len(report.requested_only),
                )
            )
            self.orphans_requested_label.setVisible(True)
        else:
            self.orphans_requested_label.setVisible(False)

        self.orphans_size_label.setText(self._size_text(report.measured, report.orphan_size_bytes))
        self.orphans_size_label.setVisible(report.measured > 0)
        self.summary_label.setText(
            self._shell.text(
                TranslationKey.DEPS_SUMMARY,
                packages=len(graph),
                orphans=report.count,
            )
        )

    def _measured_sizes(self, graph: DependencyGraph) -> dict[str, int]:
        """Measured sizes for the orphans, so the report can total them.

        Only the orphans are measured, and only when the packages view has already
        measured them: computing disk usage for every installed package just to
        decorate one line would be a cost the user did not ask for. A package with
        no measurement is reported as unmeasured rather than as zero.
        """
        sizes: dict[str, int] = {}
        for name, size in self._size_source.items():
            if size is None:
                continue
            sizes[normalize_name(name)] = int(size)
        return sizes

    def set_size_source(self, sizes: dict[str, int | None]) -> None:
        """Give the view a name -> bytes mapping to total the orphan sizes with."""
        self._size_source = sizes

    def _size_text(self, measured: int, total: int) -> str:
        text = self._shell.text(
            TranslationKey.DEPS_ORPHANS_SIZE,
            size=format_bytes(total),
            count=measured,
        )
        return f"{text} ({self._shell.text(TranslationKey.DEPS_ORPHANS_PARTIAL)})"

    # -- removal ---------------------------------------------------------------

    def preview_orphan_removal(self) -> bool:
        """Show the dry run for the orphan set, then ask for the removal.

        Two separate answers are required: this dialog describes what would
        happen, and the confirmation that follows shows the exact command. The
        action is emitted rather than run, so the packages view stays the only
        place that can start a change.
        """
        graph = self._graph
        if graph is None:
            return False

        report = graph.orphans()
        if not report.orphans:
            return False

        preview = graph.preview_removal(report.orphans)
        dialog = RemovalPreviewDialog(
            preview=preview,
            translator=self._shell.translator,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        environment = self._context.default_environment
        action: PackageAction = self._context.service_for(environment).action_for_uninstall_many(
            preview.would_remove
        )
        self.removal_requested.emit(action)
        return True

    # -- re-translation --------------------------------------------------------

    def retranslate(self) -> None:
        """Re-render every string the page owns in the current language."""
        for key, box in self._groups.items():
            box.setTitle(self._shell.text(key))

        self.title_label.setText(self._shell.text(TranslationKey.DEPS_TITLE))
        self.rescan_button.setText(self._shell.text(TranslationKey.DEPS_REFRESH))
        self.prompt_label.setText(self._shell.text(TranslationKey.DEPS_SELECT_PROMPT))
        self.search_edit.setPlaceholderText(self._shell.text(TranslationKey.DEPS_SEARCH))

        if self._graph is not None:
            self._populate_packages(self._graph)
            self._update_orphans(self._graph)
            self._update_details(self._selected_key or None)
