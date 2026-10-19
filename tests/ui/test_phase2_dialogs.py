"""The two phase-2 dialogs: the imported-file review and the disk analysis.

Both are pure presentation over data a test can build: one ratifies a
parser's per-line decisions, the other draws measured numbers. Their tests
drive them directly, without a file picker or a walk behind them - which is
also how the row-mapping bug these tests caught is kept from coming back: a
comment anywhere above a package used to shift every selection onto the
wrong line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from pypackhub.core.disk_usage import CacheUsage, EnvironmentUsage
from pypackhub.core.sizes import PackageSize
from pypackhub.core.validation import parse_requirements
from pypackhub.ui.i18n.translator import Translator
from pypackhub.ui.widgets.dialogs import DiskUsageDialog, ImportPreviewDialog

pytestmark = pytest.mark.gui


def _parsed(*lines: str):
    return parse_requirements("\n".join(lines) + "\n", source="requirements.txt")


def _check(dialog: ImportPreviewDialog, text: str, state: Qt.CheckState) -> None:
    """Set one row's check state, found by its text."""
    for row in range(dialog.list_widget.count()):
        item = dialog.list_widget.item(row)
        if item is not None and item.text() == text:
            item.setCheckState(state)
            return
    raise AssertionError(f"no row shows {text!r}")


def test_the_review_starts_with_the_parsers_decisions(qapp: QApplication) -> None:
    """Plain packages checked, flagged packages unchecked, options inert."""
    del qapp
    dialog = ImportPreviewDialog(
        parsed=_parsed(
            "# a comment first, to shift every row that follows",
            "requests==2.31.0",
            "--index-url https://mirror.example/simple",
            "urllib3==2.2.0",
            "not a valid spec!!",
        ),
        translator=Translator(),
    )

    # The comment is not a decision and never appears.
    texts = [dialog.list_widget.item(row).text() for row in range(dialog.list_widget.count())]
    assert "requests==2.31.0" in texts
    assert all("comment" not in text for text in texts)

    # Both plain packages start checked - in list order, not parsed order,
    # which is the mapping a comment above them used to break.
    assert dialog.selected_specs() == ("requests==2.31.0", "urllib3==2.2.0")

    # The refused line cannot be selected; the option line cannot either,
    # because this flow installs specs and cannot honour installer options.
    for row in range(dialog.list_widget.count()):
        item = dialog.list_widget.item(row)
        assert item is not None
        if "index-url" in item.text() or "not a valid spec" in item.text():
            assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable

    # Unchecking is how a line is left out of the batch.
    _check(dialog, "urllib3==2.2.0", Qt.CheckState.Unchecked)
    assert dialog.selected_specs() == ("requests==2.31.0",)


def test_the_footer_and_button_follow_the_selection(qapp: QApplication) -> None:
    """Nothing selected means the only way out is Cancel, by design."""
    del qapp
    dialog = ImportPreviewDialog(parsed=_parsed("a==1.0", "b==2.0"), translator=Translator())

    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and ok.isEnabled()
    assert "2" in dialog.count_label.text()

    _check(dialog, "a==1.0", Qt.CheckState.Unchecked)
    _check(dialog, "b==2.0", Qt.CheckState.Unchecked)

    assert ok is not None and not ok.isEnabled()
    assert ok.text() == "Nothing is selected"


def test_the_disk_dialog_draws_the_measured_numbers(qapp: QApplication, tmp_path: Path) -> None:
    """Totals, largest-first bars, and the caches - nothing invented."""
    del qapp
    usage = EnvironmentUsage(
        environment=tmp_path,
        total_bytes=12_345,
        files=7,
        complete=False,
        packages=(
            PackageSize(
                name="large",
                location=tmp_path / "large",
                files_count=3,
                size_bytes=900,
                top_paths=(),
                exact=True,
            ),
            PackageSize(
                name="small",
                location=tmp_path / "small",
                files_count=1,
                size_bytes=100,
                top_paths=(),
                exact=True,
            ),
        ),
        caches=(
            CacheUsage(kind="pip", path=tmp_path / "pip", size_bytes=4096, exists=True),
            CacheUsage(kind="uv", path=tmp_path / "uv", size_bytes=0, exists=False),
        ),
    )
    dialog = DiskUsageDialog(usage=usage, translator=Translator())

    from PySide6.QtWidgets import QLabel

    labels = dialog.findChildren(QLabel)
    assert labels
    joined = " ".join(label.text() for label in labels)

    assert "12.1 KiB" in joined  # the environment total
    assert "large" in joined
    assert "small" in joined
    assert "not created" in joined  # a cache never built is a fact, not an error
