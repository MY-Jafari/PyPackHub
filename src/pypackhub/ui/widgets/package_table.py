"""The packages table: model, filter proxy and status badge.

Qt's model/view split is used properly here rather than stuffing items into a
widget: a model that knows the inventory, a proxy that filters and sorts it, and
a delegate that paints the status pill. That is what makes a 300-package list
cheap to filter and re-sort, and it is what lets the table fill in progressively
while PyPI lookups are still running.

The model holds no translator of its own logic: it is given a translator and a
token set, because both change live (language and theme switches) and the view
has to be able to re-render without rebuilding the table.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any, Final

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QWidget

from pypackhub.core.inventory import Inventory, PackageRow, PackageStatus
from pypackhub.ui.formatting import UNKNOWN_TEXT, format_bytes, version_sort_key
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator
from pypackhub.ui.theme.design_tokens import AccentRole, DesignTokens

#: Qt hands the delegate and the proxy either kind of index; both expose the same
#: API, so the signatures accept the union and the code treats them alike.
AnyIndex = QModelIndex | QPersistentModelIndex

__all__ = [
    "COLUMNS",
    "ROW_ROLE",
    "SIZE_ROLE",
    "SORT_ROLE",
    "STATUS_ROLE",
    "BadgeDelegate",
    "Column",
    "PackageFilterProxy",
    "PackageTableModel",
    "build_model",
    "status_role",
]

#: Role the proxy sorts with. Returning a purpose-built key per column is what
#: makes the version column sort as versions instead of as text.
SORT_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 1

#: Role that exposes the row object itself, so a view can act on a selection
#: without re-locating it in the inventory.
ROW_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 2

#: Role carrying the row's :class:`PackageStatus`, so the badge delegate knows
#: which pill to paint. It holds the status rather than the colour: the delegate
#: resolves the colour through the tokens, which is what makes a theme switch
#: repaint every badge.
STATUS_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 3

#: Role that carries the raw byte count, for a size tooltip.
SIZE_ROLE: Final = int(Qt.ItemDataRole.UserRole) + 4


class Column(str, Enum):
    """The table's columns, in display order."""

    NAME = "name"
    VERSION = "version"
    LATEST = "latest"
    STATUS = "status"
    SIZE = "size"
    SUMMARY = "summary"


#: (column, header translation key, width in pixels). A width of 0 lets the
#: column size itself to the content.
COLUMNS: Final[tuple[tuple[Column, TranslationKey, int], ...]] = (
    (Column.NAME, TranslationKey.PACKAGES_NAME, 260),
    (Column.VERSION, TranslationKey.PACKAGES_VERSION, 120),
    (Column.LATEST, TranslationKey.PACKAGES_LATEST, 120),
    (Column.STATUS, TranslationKey.PACKAGES_STATUS, 120),
    (Column.SIZE, TranslationKey.PACKAGES_SIZE, 100),
    (Column.SUMMARY, TranslationKey.PACKAGES_SUMMARY, 0),
)

_STATUS_ROLES: Final[dict[PackageStatus, AccentRole]] = {
    PackageStatus.UP_TO_DATE: AccentRole.UP_TO_DATE,
    PackageStatus.OUTDATED: AccentRole.OUTDATED,
    PackageStatus.UNKNOWN: AccentRole.NEUTRAL,
}

_STATUS_KEYS: Final[dict[PackageStatus, TranslationKey]] = {
    PackageStatus.UP_TO_DATE: TranslationKey.STATUS_UP_TO_DATE,
    PackageStatus.OUTDATED: TranslationKey.STATUS_OUTDATED,
    PackageStatus.UNKNOWN: TranslationKey.STATUS_UNKNOWN,
}

#: Sort rank per status: outdated first when a user asks for it, and stable for
#: every status otherwise.
_STATUS_RANK: Final[dict[PackageStatus, int]] = {
    PackageStatus.OUTDATED: 0,
    PackageStatus.UNKNOWN: 1,
    PackageStatus.UP_TO_DATE: 2,
}


def status_role(status: PackageStatus | str) -> AccentRole:
    """The badge colour role for a status.

    *status* arrives either as the enum member or as its plain string value.
    A ``str``-based enum does not survive the trip through Qt: a
    :class:`QSortFilterProxyModel` hands the value back as a ``str``, which is
    what the delegate sees. Both forms are therefore accepted and normalised
    through the enum, rather than relying on the fact that a ``str`` member and
    its value happen to hash alike.

    An unrecognised value degrades to the neutral role instead of raising: this
    runs inside a paint handler, where an exception prints a traceback and
    leaves the cell blank rather than doing anything useful.
    """

    try:
        canonical = PackageStatus(status)
    except ValueError:
        return _STATUS_ROLES[PackageStatus.UNKNOWN]

    return _STATUS_ROLES[canonical]


class PackageTableModel(QAbstractTableModel):
    """The installed packages, as a table."""

    def __init__(
        self,
        *,
        translator: Translator,
        tokens: DesignTokens,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[PackageRow] = []
        self._translator = translator
        self._tokens = tokens
        self._inventory: Inventory | None = None

    # -- Qt model API --------------------------------------------------

    def rowCount(self, parent: AnyIndex | None = None) -> int:  # noqa: N802 - Qt override
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: AnyIndex | None = None) -> int:  # noqa: N802 - Qt override
        if parent is not None and parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if orientation is not Qt.Orientation.Horizontal:
            return None
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if 0 <= section < len(COLUMNS):
            return self._translator.translate(COLUMNS[section][1])
        return None

    def data(self, index: AnyIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None

        row = self._rows[index.row()]
        column = COLUMNS[index.column()][0]

        if role == ROW_ROLE:
            return row
        if role == SORT_ROLE:
            return self._sort_value(row, column)
        if role == STATUS_ROLE:
            return row.status
        if role == SIZE_ROLE:
            return row.size_bytes
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return self._tooltip(row, column)
        if role == int(Qt.ItemDataRole.ForegroundRole):
            return self._foreground(row, column)
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None

        return self._display(row, column)

    def flags(self, index: AnyIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # -- model data ----------------------------------------------------

    @property
    def inventory(self) -> Inventory | None:
        return self._inventory

    @property
    def rows(self) -> tuple[PackageRow, ...]:
        return tuple(self._rows)

    def row_at(self, row_index: int) -> PackageRow | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    def set_inventory(self, inventory: Inventory | None) -> None:
        """Replace the whole table with a new inventory snapshot."""
        self.beginResetModel()
        self._inventory = inventory
        self._rows = list(inventory.rows) if inventory is not None else []
        self.endResetModel()

    def update_row(self, row: PackageRow) -> None:
        """Replace one row in place, so the table fills in progressively."""
        canonical = _normalized(row.name)
        for index, existing in enumerate(self._rows):
            if _normalized(existing.name) == canonical:
                self._rows[index] = row
                top_left = self.index(index, 0)
                bottom_right = self.index(index, len(COLUMNS) - 1)
                self.dataChanged.emit(top_left, bottom_right)
                if self._inventory is not None:
                    self._inventory = self._inventory.replace_rows(self._rows)
                return

    def set_translator(self, translator: Translator) -> None:
        """Re-render after a language switch."""
        self._translator = translator
        self._refresh_all()

    def set_tokens(self, tokens: DesignTokens) -> None:
        """Re-render after a theme switch (badge colours come from the tokens)."""
        self._tokens = tokens
        self._refresh_all()

    def _refresh_all(self) -> None:
        if not self._rows:
            return
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(COLUMNS) - 1)
        top_left = self.index(0, 0)
        bottom_right = self.index(len(self._rows) - 1, len(COLUMNS) - 1)
        self.dataChanged.emit(top_left, bottom_right)

    # -- rendering -----------------------------------------------------

    def _display(self, row: PackageRow, column: Column) -> str:
        if column is Column.NAME:
            return row.name
        if column is Column.VERSION:
            return row.installed or UNKNOWN_TEXT
        if column is Column.LATEST:
            if row.error:
                return UNKNOWN_TEXT
            return row.latest or UNKNOWN_TEXT
        if column is Column.STATUS:
            return self._translator.translate(_STATUS_KEYS[row.status])
        if column is Column.SIZE:
            return format_bytes(row.size_bytes)
        return row.summary or ""

    def _sort_value(self, row: PackageRow, column: Column) -> Any:
        if column is Column.NAME:
            return _normalized(row.name)
        if column is Column.VERSION:
            return version_sort_key(row.installed)
        if column is Column.LATEST:
            return version_sort_key(row.latest)
        if column is Column.STATUS:
            return _STATUS_RANK[row.status]
        if column is Column.SIZE:
            return row.sort_size_bytes
        return row.summary.lower()

    def _tooltip(self, row: PackageRow, column: Column) -> str:
        if column is Column.SIZE and row.size_bytes is not None:
            return f"{row.size_bytes:,} B"
        if column is Column.SUMMARY:
            return row.summary
        if column is Column.LATEST and row.error:
            return row.error
        return row.name

    def _foreground(self, row: PackageRow, column: Column) -> QColor | None:
        if column is Column.STATUS:
            # The badge delegate paints the pill; the text colour is left alone.
            return None
        if column is Column.LATEST and row.is_outdated:
            return QColor(self._tokens.color_for_role(AccentRole.OUTDATED))
        if row.error and column in {Column.LATEST, Column.VERSION}:
            return QColor(self._tokens.text_muted)
        if column is Column.NAME and row.direct_reference:
            return QColor(self._tokens.text_secondary)
        return None

    def badge_color(self, status: PackageStatus | str) -> QColor:
        """The colour the delegate should paint for a status.

        The argument is whatever came back from :data:`STATUS_ROLE`, which Qt may
        have flattened to a string - see :func:`status_role`.
        """

        return QColor(self._tokens.color_for_role(status_role(status)))

    @property
    def badge_text_color(self) -> QColor:
        return QColor(self._tokens.text_on_accent)


class PackageFilterProxy(QSortFilterProxyModel):
    """Filter and sort the packages table.

    Filtering matches the package name and its summary, ignores case, and can be
    narrowed to outdated packages only - which is the same switch the settings
    layer already persists as ``show_outdated_by_default``.
    """

    queryChanged = Signal(str)  # noqa: N815 - Qt signal naming
    outdatedFilterChanged = Signal(bool)  # noqa: N815

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._query = ""
        self._outdated_only = False
        self.setDynamicSortFilter(True)
        self.setSortRole(SORT_ROLE)

    @property
    def query(self) -> str:
        return self._query

    @property
    def outdated_only(self) -> bool:
        return self._outdated_only

    def set_query(self, query: str) -> None:
        query = query.strip()
        if query == self._query:
            return
        with self._filter_change():
            self._query = query
        self.queryChanged.emit(query)

    def set_outdated_only(self, outdated_only: bool) -> None:
        if outdated_only == self._outdated_only:
            return
        with self._filter_change():
            self._outdated_only = outdated_only
        self.outdatedFilterChanged.emit(outdated_only)

    @contextmanager
    def _filter_change(self) -> Iterator[None]:
        """Re-run the filter around a change to its criteria.

        Qt 6.10 replaced ``invalidateFilter()`` - and, in 6.11, its own
        ``invalidateRowsFilter()`` replacement - with the
        ``beginFilterChange()`` / ``endFilterChange()`` pair, which reports the
        rows that appear and disappear instead of making the view rebuild
        everything. Both are still only warnings, but this project turns a
        deprecation warning from its own code into a test failure, and the
        supported range in ``pyproject.toml`` still includes PySide6 6.8, where
        the pair does not exist yet.

        So the pair is used when it is there and ``invalidateFilter()`` is the
        fallback. The body of the ``with`` block is where the criteria change:
        the new API has to bracket the assignment to report it correctly.
        """

        begin = getattr(self, "beginFilterChange", None)
        end = getattr(self, "endFilterChange", None)

        if callable(begin) and callable(end):
            begin()
            yield
            end(QSortFilterProxyModel.Direction.Rows)
            return

        yield
        self.invalidateFilter()  # pragma: no cover - PySide6 < 6.10 only

    def filterAcceptsRow(  # noqa: N802 - Qt override
        self, source_row: int, source_parent: AnyIndex
    ) -> bool:
        model = self.sourceModel()
        if not isinstance(model, PackageTableModel):
            return True

        row = model.row_at(source_row)
        if row is None:
            return False

        if self._outdated_only and not row.is_outdated:
            # A package whose lookups have not answered yet stays visible: it might
            # be outdated, and hiding every unasked row would empty the table for
            # as long as the index takes to answer.
            return row.status is PackageStatus.UNKNOWN

        if not self._query:
            return True

        needle = self._query.lower()
        return needle in row.name.lower() or needle in row.summary.lower()

    def lessThan(self, left: AnyIndex, right: AnyIndex) -> bool:  # noqa: N802
        """Sort with the model's per-column sort key, never as raw text."""
        left_value = left.data(SORT_ROLE)
        right_value = right.data(SORT_ROLE)

        if left_value is None or right_value is None:
            return left_value is None and right_value is not None

        try:
            return bool(left_value < right_value)
        except TypeError:  # pragma: no cover - mixed types only from a future column
            return str(left_value) < str(right_value)

    def selected_rows(self, indexes: list[QModelIndex]) -> tuple[PackageRow, ...]:
        """The distinct rows behind a selection, in table order."""
        seen: set[str] = set()
        rows: list[PackageRow] = []

        for index in sorted(indexes, key=lambda item: item.row()):
            row = index.data(ROW_ROLE)
            if isinstance(row, PackageRow) and _normalized(row.name) not in seen:
                seen.add(_normalized(row.name))
                rows.append(row)

        return tuple(rows)


class BadgeDelegate(QStyledItemDelegate):
    """Paints a status cell as a coloured pill.

    The colour comes from the model (which got it from the design tokens), so a
    theme switch repaints correctly and no colour is hard-coded here.
    """

    #: How much of the cell height the pill occupies.
    HEIGHT_RATIO: Final = 0.62
    PADDING: Final = 14

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: AnyIndex,
    ) -> None:
        if isinstance(index, QPersistentModelIndex):
            model = index.model()
            index = model.index(index.row(), index.column()) if model is not None else QModelIndex()

        status = index.data(STATUS_ROLE)
        model = index.model()

        if status is None or not isinstance(model, PackageFilterProxy):
            super().paint(painter, option, index)
            return

        source_model = model.sourceModel()
        if not isinstance(source_model, PackageTableModel):
            super().paint(painter, option, index)
            return

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        metrics = option.fontMetrics
        width = metrics.horizontalAdvance(text) + self.PADDING * 2
        height = max(int(option.rect.height() * self.HEIGHT_RATIO), metrics.height())
        pill = QRect(
            option.rect.center().x() - width // 2,
            option.rect.center().y() - height // 2,
            width,
            height,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(source_model.badge_color(status))
        radius = height / 2
        painter.drawRoundedRect(pill, radius, radius)

        painter.setPen(source_model.badge_text_color)
        painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()


def _normalized(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def build_model(
    *, translator: Translator, tokens: DesignTokens, parent: QWidget | None = None
) -> tuple[PackageTableModel, PackageFilterProxy]:
    """Create the model and its proxy, already wired to each other."""
    model = PackageTableModel(translator=translator, tokens=tokens, parent=parent)
    proxy = PackageFilterProxy(parent)
    proxy.setSourceModel(model)
    return model, proxy
