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
        "pypackhub.ui.views.environments.detected_environments",
        lambda roots, registered=(): _listing(),
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

    def failing_scan(_roots: object, *, registered: object = ()) -> object:
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

    def slow_scan(roots: object, *, registered: object = ()) -> object:
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


# -- registering a folder, and forgetting it --------------------------------------


def _choose(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    """Point the file dialog at *folder*; the view reads it lazily, so the
    monkeypatch lands on the class the import will find."""
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(folder)),
    )


def _poetry_project(tmp_path: Path) -> Path:
    project = tmp_path / "code" / "app"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    return project


def test_registering_a_project_adds_it_to_the_settings(
    qtbot, view: EnvironmentsView, monkeypatch, tmp_path: Path
) -> None:
    """The one way to reach poetry, pipenv and pdm: point the application at
    the project folder. It is remembered, and the provider learns it so the
    switcher's next discovery offers it."""
    del qtbot
    shell = view._shell
    project = _poetry_project(tmp_path)
    _choose(monkeypatch, project)

    view._add_folder()

    assert str(project) in shell.settings.registered_environments
    assert Path(project) in view._context.provider._registered
    # and it is on disk, exactly as it was: registering never touches it
    assert (project / "pyproject.toml").is_file()


def test_registering_persists_across_a_restart(
    qtbot, view: EnvironmentsView, monkeypatch, tmp_path: Path
) -> None:
    """The list survives the launch: the folder is written through the shell's
    store, and a fresh shell reads it back."""
    del qtbot
    project = _poetry_project(tmp_path)
    _choose(monkeypatch, project)

    view._add_folder()

    revived = WindowShell(store=QSettingsStore(path=view._shell._store.path))
    revived.load()
    assert str(project) in revived.settings.registered_environments


def test_a_folder_that_is_nothing_is_refused_with_the_reason(
    qtbot, view: EnvironmentsView, monkeypatch, tmp_path: Path
) -> None:
    """A plain folder is not an environment and not a project: the honest
    answer is the refusal, not a card that lists nothing."""
    del qtbot
    shell = view._shell
    plain = tmp_path / "plain"
    plain.mkdir()
    _choose(monkeypatch, plain)

    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))
    before = shell.settings.registered_environments

    view._add_folder()

    assert toasts and toasts[0][1] is ToastKind.ERROR
    assert shell.settings.registered_environments == before


def test_cancelling_the_dialog_changes_nothing(qtbot, view: EnvironmentsView, monkeypatch) -> None:
    del qtbot
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        staticmethod(lambda *args, **kwargs: ""),
    )

    view._add_folder()

    assert view._shell.settings.registered_environments == ()


def test_registering_twice_is_reported_not_duplicated(
    qtbot, view: EnvironmentsView, monkeypatch, tmp_path: Path
) -> None:
    del qtbot
    project = _poetry_project(tmp_path)
    _choose(monkeypatch, project)
    view._add_folder()

    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))
    view._add_folder()

    assert view._shell.settings.registered_environments.count(str(project)) == 1
    assert any(kind is ToastKind.INFO for _message, kind in toasts)


def test_forgetting_removes_only_the_list_entry(
    qtbot, view: EnvironmentsView, monkeypatch, tmp_path: Path
) -> None:
    """The promise the page makes: forgetting edits the list, and the folder
    on disk is untouched - which is also why the delete affordance never
    applies to a project."""
    del qtbot
    project = _poetry_project(tmp_path)
    _choose(monkeypatch, project)
    view._add_folder()

    view._forget(str(project))

    assert view._shell.settings.registered_environments == ()
    assert view._context.provider._registered == ()
    assert project.is_dir() and (project / "pyproject.toml").is_file()
