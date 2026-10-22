"""Shared setup for the UI tests: the suite must not depend on the machine.

Two things made a UI test depend on what happens to be installed on the
machine it runs on, and the difference between a developer's box and a CI
runner is enough to turn one into a hang.

The settings page asks a *modal* question when the chosen manager is
missing - and a real question in a test blocks the runner until somebody
clicks it, which on CI means the job sits there until the six-hour limit.
The question is asked only when the tool is really absent, so a developer
with uv installed and a runner without it execute different code: the
same test passed on one machine and froze on the other.

Both are removed here. The managers count as installed, so the page never
reaches for the question; and the modal helper answers itself instead of
waiting for a click, so a test that reaches it another way cannot freeze
the suite either. A test that cares about the question patches it itself,
and its own patch is the one that counts - these are defaults, not rules.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def _independent_of_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the machine's installed managers irrelevant to every UI test."""
    monkeypatch.setattr(
        "pypackhub.ui.views.settings.manager_available",
        lambda name: True,
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
