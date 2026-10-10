"""The packages table: its model, its proxy and the delegate that paints badges.

The delegate is the reason this file exists. :data:`STATUS_ROLE` travels through
Qt's variant layer, and a ``str``-based enum does not survive the trip: a
``QSortFilterProxyModel`` hands the value back as a plain ``str``. The colour
lookup was keyed by the enum, so every badge raised ``KeyError`` inside
``paint()``. PySide6 catches exceptions from a Python override, prints the
traceback and carries on, so the table looked *almost* right, the test-suite
stayed green, and the only visible symptom was a traceback on stderr - which is
exactly how the packaging smoke test found it.

These tests call the delegate's ``paint`` directly, where an exception fails the
test instead of scrolling past as a log line, and they pin the two ends of the
contract that broke: the role carries a *status*, and the status resolves to the
same colour whether it arrives as the member or as its value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from pypackhub.core.inventory import Inventory, PackageRow, PackageStatus
from pypackhub.core.settings import Language
from pypackhub.ui.i18n.translator import Translator
from pypackhub.ui.theme.design_tokens import AccentRole, default_tokens
from pypackhub.ui.widgets.package_table import (
    STATUS_ROLE,
    BadgeDelegate,
    Column,
    PackageFilterProxy,
    PackageTableModel,
    build_model,
    status_role,
)

pytestmark = pytest.mark.gui

STATUS_COLUMN = list(Column).index(Column.STATUS)

#: What each status has to look like, stated once so a change to either side of
#: the mapping is a deliberate edit rather than a silent recolouring.
EXPECTED_ROLES = {
    PackageStatus.UP_TO_DATE: AccentRole.UP_TO_DATE,
    PackageStatus.OUTDATED: AccentRole.OUTDATED,
    PackageStatus.UNKNOWN: AccentRole.NEUTRAL,
}


def make_row(
    name: str,
    status: PackageStatus,
    *,
    installed: str = "1.0.0",
    latest: str = "",
    summary: str = "",
) -> PackageRow:
    return PackageRow(
        name=name,
        installed=installed,
        latest=latest,
        status=status,
        size_bytes=1024,
        summary=summary,
    )


@pytest.fixture
def rows() -> tuple[PackageRow, ...]:
    return (
        make_row(
            "requests",
            PackageStatus.OUTDATED,
            installed="2.31.0",
            latest="2.32.0",
            summary="HTTP for Humans",
        ),
        make_row(
            "urllib3",
            PackageStatus.UP_TO_DATE,
            installed="2.2.0",
            latest="2.2.0",
            summary="HTTP library",
        ),
        make_row("mystery", PackageStatus.UNKNOWN, summary="no index entry"),
    )


@pytest.fixture
def table(
    qapp: QApplication, rows: tuple[PackageRow, ...], tmp_path: Path
) -> tuple[PackageTableModel, PackageFilterProxy]:
    del qapp  # the fixture is only here to guarantee a QApplication exists
    model, proxy = build_model(translator=Translator(language=Language.EN), tokens=default_tokens())
    model.set_inventory(Inventory(environment=tmp_path / "env", backend="pip", rows=rows))
    return model, proxy


# --------------------------------------------------------------------------- #
# The colour lookup                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", list(PackageStatus))
def test_status_role_maps_the_member(status: PackageStatus) -> None:
    assert status_role(status) is EXPECTED_ROLES[status]


@pytest.mark.parametrize("status", list(PackageStatus))
def test_status_role_maps_the_plain_string_value(status: PackageStatus) -> None:
    """The form the value really takes once it has been through the proxy."""

    assert status_role(status.value) is EXPECTED_ROLES[status]


def test_status_role_does_not_rely_on_enum_identity() -> None:
    """A status and its value must not be interchangeable *roles*.

    ``AccentRole.OUTDATED`` shares the value ``"warning"`` with nothing in
    :class:`PackageStatus`, so handing a role where a status is expected has to
    degrade instead of silently painting the wrong colour.
    """

    assert status_role(AccentRole.OUTDATED) is EXPECTED_ROLES[PackageStatus.UNKNOWN]


@pytest.mark.parametrize("value", ["", "nonsense", "None", "0"])
def test_status_role_degrades_instead_of_raising(value: str) -> None:
    """A paint handler must not raise: a bad value must not blank the cell."""

    assert status_role(value) is EXPECTED_ROLES[PackageStatus.UNKNOWN]


# --------------------------------------------------------------------------- #
# What the role carries                                                        #
# --------------------------------------------------------------------------- #


def test_status_role_carries_the_status_not_the_colour(
    table: tuple[PackageTableModel, PackageFilterProxy],
) -> None:
    """The model hands over a status; the delegate resolves the colour.

    Returning the colour role here is the shape of the original defect: the
    delegate then fed it back into the status lookup, and the whole badge column
    raised on the first repaint.
    """

    model, _proxy = table
    assert model.data(model.index(0, STATUS_COLUMN), STATUS_ROLE) is PackageStatus.OUTDATED


def test_status_role_comes_back_from_the_proxy_unchanged_in_meaning(
    table: tuple[PackageTableModel, PackageFilterProxy],
) -> None:
    """Qt flattens the enum to a ``str``; the meaning has to survive that."""

    _, proxy = table
    value = proxy.index(0, STATUS_COLUMN).data(STATUS_ROLE)

    assert isinstance(value, str)
    assert status_role(value) is AccentRole.OUTDATED


# --------------------------------------------------------------------------- #
# The delegate                                                                 #
# --------------------------------------------------------------------------- #


def test_badge_delegate_paints_every_status(
    qapp: QApplication, table: tuple[PackageTableModel, PackageFilterProxy]
) -> None:
    """The regression test: paint every row, through the proxy, without raising.

    Before the fix this raised ``KeyError: 'warning'`` for the first row and
    produced a traceback for each of the others.
    """

    del qapp
    _, proxy = table
    delegate = BadgeDelegate()

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 140, 28)
    surface = QPixmap(option.rect.size())
    surface.fill()

    for row in range(proxy.rowCount()):
        index = proxy.index(row, STATUS_COLUMN)
        painter = QPainter(surface)
        try:
            delegate.paint(painter, option, index)
        finally:
            painter.end()


def test_badge_delegate_falls_back_for_an_empty_cell(
    qapp: QApplication, table: tuple[PackageTableModel, PackageFilterProxy]
) -> None:
    """A status cell with nothing in it paints the default cell, not a badge."""

    del qapp
    _, proxy = table
    delegate = BadgeDelegate()

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 140, 28)
    surface = QPixmap(option.rect.size())
    surface.fill()

    painter = QPainter(surface)
    try:
        delegate.paint(painter, option, proxy.index(0, list(Column).index(Column.NAME)))
    finally:
        painter.end()


# --------------------------------------------------------------------------- #
# The proxy                                                                    #
# --------------------------------------------------------------------------- #


def test_filter_matches_the_summary_as_well_as_the_name(
    table: tuple[PackageTableModel, PackageFilterProxy],
) -> None:
    """Searching for text that only appears in a summary still finds the row."""

    _, proxy = table

    proxy.set_query("humans")
    assert [proxy.index(row, 0).data() for row in range(proxy.rowCount())] == ["requests"]


def test_filter_narrows_to_the_requested_rows(
    table: tuple[PackageTableModel, PackageFilterProxy],
) -> None:
    _, proxy = table

    proxy.set_query("requests")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "requests"

    proxy.set_query("")
    assert proxy.rowCount() == 3


def test_outdated_filter_uses_the_status(
    table: tuple[PackageTableModel, PackageFilterProxy],
) -> None:
    _, proxy = table

    proxy.set_outdated_only(True)
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data() == "requests"


def test_version_column_sorts_as_a_version(qapp: QApplication, tmp_path: Path) -> None:
    """The sort role returns a version key, so 1.10 sorts after 1.9, not before."""

    del qapp
    model, proxy = build_model(translator=Translator(language=Language.EN), tokens=default_tokens())
    model.set_inventory(
        Inventory(
            environment=tmp_path / "env",
            backend="pip",
            rows=(
                make_row("newer", PackageStatus.UP_TO_DATE, installed="1.10.0"),
                make_row("older", PackageStatus.UP_TO_DATE, installed="1.9.0"),
            ),
        )
    )

    proxy.sort(list(Column).index(Column.VERSION))
    names = [proxy.index(row, 0).data() for row in range(proxy.rowCount())]
    assert names == ["older", "newer"]
