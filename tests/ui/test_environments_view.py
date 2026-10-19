"""The environments page discovers off the GUI thread.

Discovery walks the standard roots and the working directory; doing that in
the constructor froze the window before its first frame - the constructor
runs before the first paint, and a slow or networked home makes the walk
take seconds. The listing now arrives from a worker thread, and these tests
pin that: what renders, what a refresh reports, and that a scan already
running is not started twice.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from pypackhub.core.environments import (
    EnvironmentListing,
    EnvironmentSource,
    ListedEnvironment,
)
from pypackhub.core.platform_guard import EnvironmentKind, InterpreterFacts
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.environments import EnvironmentsView
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell
from pypackhub.ui.widgets.toast import ToastKind

pytestmark = pytest.mark.gui

PREFIX = Path("/fake/env").resolve()


def _listing() -> EnvironmentListing:
    """One scan's answer: a single venv the discovery supposedly found."""
    return EnvironmentListing(
        environments=(
            ListedEnvironment(
                prefix=PREFIX,
                facts=InterpreterFacts(
                    prefix=PREFIX,
                    executable=PREFIX / "Scripts" / "python.exe",
                    kind=EnvironmentKind.VENV,
                    externally_managed=False,
                    externally_managed_marker=None,
                    is_alias_stub=False,
                ),
                source=EnvironmentSource.SCAN,
            ),
        ),
        scanned_roots=(PREFIX.parent,),
    )


@pytest.fixture
def store(tmp_path: Path) -> QSettingsStore:
    return QSettingsStore(path=tmp_path / "settings.ini")


@pytest.fixture
def view(qapp, store, monkeypatch) -> EnvironmentsView:
    """A page whose discovery is pinned to the fake listing above.

    The real scan is what this file keeps off the GUI thread; pinning its
    answer keeps the tests about the threading, not about whatever
    environments happen to be on the machine that runs them.
    """
    monkeypatch.setattr(
        "pypackhub.ui.views.environments.detected_environments", lambda roots: _listing()
    )
    shell = WindowShell(store=store)
    shell.load()
    return EnvironmentsView(context=UiContext(), shell=shell)


def test_the_listing_arrives_from_the_worker_and_renders(qtbot, view: EnvironmentsView) -> None:
    """The constructor schedules rather than scans; the cards follow the signal."""
    qtbot.waitUntil(lambda: view._listing is not None, timeout=5000)
    qtbot.waitUntil(lambda: str(PREFIX) in view._cards, timeout=5000)


def test_a_refresh_reports_the_fresh_count_when_the_scan_answers(
    qtbot, view: EnvironmentsView
) -> None:
    """The toast follows the answer, not the click.

    Synchronously the count was known the moment the button was pressed;
    off the GUI thread the honest report is the one the worker brings back.
    """
    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))
    qtbot.waitUntil(lambda: view._listing is not None, timeout=5000)

    view._refresh()
    qtbot.waitUntil(lambda: bool(toasts), timeout=5000)
    assert toasts[0][1] is ToastKind.SUCCESS


def test_a_failed_scan_keeps_the_listing_and_reports_the_error(
    qtbot, view: EnvironmentsView, monkeypatch
) -> None:
    """A scan that dies on the worker leaves the page as it was, saying why."""
    qtbot.waitUntil(lambda: view._listing is not None, timeout=5000)

    class _ScanFailedError(Exception):
        pass

    def failing_scan(_roots: object) -> object:
        raise _ScanFailedError("the home directory is unreachable")

    monkeypatch.setattr("pypackhub.ui.views.environments.detected_environments", failing_scan)

    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))
    view._refresh()
    qtbot.waitUntil(lambda: bool(toasts), timeout=5000)

    assert toasts[0][1] is ToastKind.ERROR
    assert "unreachable" in toasts[0][0]
    assert view._listing is not None  # the old answer stays on screen


def test_a_scan_already_running_is_not_started_twice(
    qtbot, view: EnvironmentsView, monkeypatch
) -> None:
    """Rapid refreshes coalesce into one walk, not a stack of them."""
    qtbot.waitUntil(lambda: not view._load_task, timeout=5000)  # the first scan is done

    calls: list[object] = []
    release = threading.Event()

    def slow_scan(roots: object) -> object:
        calls.append(roots)
        release.wait(10.0)
        return None  # a failed scan: the page keeps what it already has

    monkeypatch.setattr("pypackhub.ui.views.environments.detected_environments", slow_scan)

    view._load()
    view._load()
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=5000)
    assert len(calls) == 1  # the second load was refused, not queued

    release.set()
    qtbot.waitUntil(lambda: not view._load_task, timeout=5000)
