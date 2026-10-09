"""The way out of a PEP 668 refusal: the dialog and the flow behind it.

Three things are checked here, and each is a rule rather than a detail:

* the dialog refuses input that ``core.environments.create_environment`` would
  refuse anyway, so the user is told in the dialog instead of in a log;
* the offer to create a virtual environment appears only for the refusal a virtual
  environment actually solves, and never as a way to click through a refusal;
* accepting the dialog schedules a real create job through the shell's log panel,
  rather than blocking the window behind a modal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog

from pypackhub.core.platform_guard import ModificationPolicy, Severity
from pypackhub.core.runner import CommandResult
from pypackhub.ui.app import build_window
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator
from pypackhub.ui.views.main_window import MainWindow
from pypackhub.ui.views.window_shell import QSettingsStore
from pypackhub.ui.widgets.dialogs import (
    BlockedDialog,
    CreateEnvironmentDialog,
    CreateEnvironmentRequest,
)

pytestmark = pytest.mark.gui


@pytest.fixture
def store(tmp_path: Path) -> QSettingsStore:
    return QSettingsStore(path=tmp_path / "settings.ini")


@pytest.fixture
def window(qapp: QApplication, store: QSettingsStore) -> MainWindow:
    built = build_window(store=store)
    yield built
    built.close()


@pytest.fixture
def translator() -> Translator:
    return Translator()


def _pep668_policy() -> ModificationPolicy:
    """The shape of refusal a virtual environment is the answer to."""
    return ModificationPolicy(
        allowed=False,
        requires_confirmation=True,
        severity=Severity.DANGER,
        reason_code="pep668_externally_managed",
        explanation="this environment is externally managed",
        suggestion="Create a virtual environment",
        would_need_installer_override=True,
    )


def _stub_policy() -> ModificationPolicy:
    """A refusal a virtual environment would not fix."""
    return ModificationPolicy(
        allowed=False,
        requires_confirmation=True,
        severity=Severity.DANGER,
        reason_code="app_execution_alias",
        explanation="this is a Windows App Execution Alias, not Python",
        suggestion="Install Python from python.org",
        would_need_installer_override=False,
    )


# -- the dialog ---------------------------------------------------------------


def test_the_dialog_refuses_an_empty_location(qapp: QApplication, translator: Translator) -> None:
    dialog = CreateEnvironmentDialog(translator=translator)

    assert dialog.value().location == ".venv"

    dialog.location_edit.setText("   ")
    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error


def test_the_dialog_refuses_a_relative_interpreter(
    qapp: QApplication, translator: Translator
) -> None:
    dialog = CreateEnvironmentDialog(translator=translator)
    dialog.interpreter_edit.setText("python")

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error


def test_the_dialog_accepts_an_absolute_interpreter(
    qapp: QApplication, translator: Translator, tmp_path: Path
) -> None:
    location = tmp_path / "example-env"
    interpreter = tmp_path / "python3"
    dialog = CreateEnvironmentDialog(translator=translator)
    dialog.location_edit.setText(str(location))
    dialog.interpreter_edit.setText(str(interpreter))

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.value() == CreateEnvironmentRequest(
        location=str(location), interpreter=str(interpreter)
    )


def test_a_blank_interpreter_means_the_running_one(
    qapp: QApplication, translator: Translator, tmp_path: Path
) -> None:
    dialog = CreateEnvironmentDialog(translator=translator)
    dialog.location_edit.setText(str(tmp_path / "example-env"))

    assert dialog.value().interpreter is None


# -- the offer on the refusal --------------------------------------------------


def test_the_pep668_refusal_offers_the_way_out(qapp: QApplication) -> None:
    dialog = BlockedDialog(
        policy=_pep668_policy(),
        translator=Translator(),
        offer_create_environment=True,
    )

    assert dialog.create_button is not None
    assert dialog.create_button.text() == Translator().translate(
        TranslationKey.BLOCKED_CREATE_ENVIRONMENT
    )
    assert dialog.wants_create_environment is False

    dialog.create_button.click()

    assert dialog.wants_create_environment is True
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_refusal_a_virtual_environment_cannot_fix_gets_no_button(
    qapp: QApplication,
) -> None:
    """An App Execution Alias stub has no interpreter to build an environment with.

    Offering the button anyway would invite the user to think the refusal was
    negotiable - the opposite of what the dialog is for.
    """
    dialog = BlockedDialog(policy=_stub_policy(), translator=Translator())

    assert dialog.create_button is None
    assert dialog.wants_create_environment is False


# -- the flow ------------------------------------------------------------------


def test_accepting_the_dialog_schedules_a_creation(window: MainWindow, monkeypatch) -> None:
    """The create job goes through the task queue, not a modal that blocks the window."""
    view = window.packages_view
    calls: list[CreateEnvironmentRequest] = []

    class FakeDialog:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def value(self) -> CreateEnvironmentRequest:
            return CreateEnvironmentRequest(location=str(Path.cwd() / "example-env"))

    def fake_create(location: Path, **kwargs: object) -> object:
        calls.append(CreateEnvironmentRequest(location=str(location)))
        return _fake_result(location)

    monkeypatch.setattr("pypackhub.ui.views.packages.CreateEnvironmentDialog", FakeDialog)
    monkeypatch.setattr("pypackhub.ui.views.packages.create_environment", fake_create)

    assert view.create_environment_requested() is True
    assert view._create_task  # a job is on the queue, and the window is not blocked

    _wait_for(lambda: not view._create_task)
    assert calls, "the request never reached the core"


def test_declining_the_dialog_schedules_nothing(window: MainWindow, monkeypatch) -> None:
    view = window.packages_view

    class FakeDialog:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Rejected

        def value(self) -> CreateEnvironmentRequest:  # pragma: no cover - not reached
            raise AssertionError("value() is read only after acceptance")

    monkeypatch.setattr("pypackhub.ui.views.packages.CreateEnvironmentDialog", FakeDialog)

    assert view.create_environment_requested() is False
    assert not view._create_task


def _wait_for(condition, *, timeout: float = 10.0) -> None:
    """Pump the Qt event loop until *condition* holds, so queued signals land."""
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtWidgets import QApplication

    timer = QElapsedTimer()
    timer.start()
    while not condition() and timer.elapsed() < timeout * 1000:
        QApplication.processEvents()
        _sleep_briefly()
    assert condition(), "the create job did not finish in time"


def _sleep_briefly() -> None:
    import time

    time.sleep(0.01)


def _fake_result(location: Path) -> object:
    from pypackhub.core.environments import CreateEnvironmentResult

    return CreateEnvironmentResult(
        prefix=Path(location).resolve(),
        requested_location=Path(location),
        interpreter=Path("/usr/bin/python3"),
        success=True,
        result=CommandResult(
            argv=("python", "-m", "venv", str(location)),
            returncode=0,
            stdout="",
            stderr="",
            duration=0.1,
        ),
        message=f"Virtual environment created at {location}.",
    )
