"""The packages view: what it shows while loading, and what it does after a change.

Four behaviours are checked here, and each one was a defect:

* the table is filled from the listing the moment it exists, instead of staying
  empty until the slowest package-index lookup answers;
* a refresh the *user* asks for while another is running is queued, not dropped;
* a refresh that is still running when a change finishes is *cancelled*, not
  waited out - it read the environment before the change, so waiting for it
  kept the table showing the version the package had before it changed, for as
  long as the stale pass's slow lookups took;
* a successful change invalidates the shared metadata index, which is what the
  detail panel and the dependency graph read instead of the package table.

The service is faked rather than stubbed down to individual calls: the view talks
to it through four methods, and a fake that keeps the version numbers in a dict is
both shorter and more honest about what the view is allowed to assume.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from pypackhub.core.backends.base import (
    ActionKind,
    BackendCapability,
    BackendError,
    CommandPlan,
    PackageAction,
)
from pypackhub.core.inventory import Inventory, PackageRow, PackageStatus
from pypackhub.core.platform_guard import (
    EnvironmentKind,
    InterpreterFacts,
    ModificationPolicy,
    Severity,
)
from pypackhub.core.runner import CommandResult
from pypackhub.core.snapshots import SnapshotStore
from pypackhub.ui.i18n.translations import EN_TABLE, TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views import packages as packages_module
from pypackhub.ui.views.packages import PackagesView
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell
from pypackhub.ui.widgets.dialogs import ConfirmationDialog, ElevationNotice, FallbackNotice
from pypackhub.ui.widgets.log_panel import LogPanel

pytestmark = pytest.mark.gui

#: A prefix that never has to exist: the fake service answers from its own dict,
#: so no test in this file touches a real environment or the network.
PREFIX = Path("/fake/env").resolve()


class FakeBackend:
    """The little the view asks of a backend besides listing."""

    capability = BackendCapability(name="pip", can_install=True, can_install_user=False)

    def download_plan(self, action: PackageAction) -> CommandPlan | None:
        return None


class FakeService:
    """The core service, with a dict where the installed versions live."""

    def __init__(self) -> None:
        self.versions = {"requests": "2.31.0", "urllib3": "2.2.0"}
        self.backend = FakeBackend()
        self.listings: list[dict[str, str]] = []
        self.invalidations = 0
        #: When set, ``refresh`` reports its listing and then waits here, which is
        #: how a refresh is held open while a change happens underneath it.
        self.hold: threading.Event | None = None
        #: When set, ``refresh`` fails *after* reporting its listing, which is the
        #: shape of a listing that worked followed by an index that did not.
        self.fail_after_listing = False
        #: Whether the backend needs administrator rights for its actions.
        self.needs_elevation = False
        #: What ``execute`` was asked for, in order.
        self.elevated_calls: list[bool] = []
        #: When False, the backend cannot change what the interpreter imports
        #: for its targets (the user-site shadowing case).
        self.can_reach_targets = True
        #: When set, the pip fallback cannot be planned (no pip on the machine).
        self.fallback_raises = False

    @property
    def facts(self) -> InterpreterFacts:
        return InterpreterFacts(
            prefix=PREFIX,
            executable=PREFIX / "bin" / "python",
            kind=EnvironmentKind.VENV,
            externally_managed=False,
            externally_managed_marker=None,
            is_alias_stub=False,
        )

    @property
    def backend_name(self) -> str:
        return self.backend.capability.name

    def policy(self) -> ModificationPolicy:
        return ModificationPolicy(
            allowed=True,
            requires_confirmation=False,
            severity=Severity.OK,
            reason_code="ok",
            explanation="this environment may be changed",
        )

    def refresh(
        self,
        *,
        with_sizes: bool,
        with_latest: bool,
        on_row=None,
        on_listed=None,
        cancel=None,
    ) -> Inventory:
        self.listings.append(dict(self.versions))
        rows = tuple(
            PackageRow(
                name=name,
                installed=version,
                latest="2.32.0",
                status=PackageStatus.OUTDATED,
                location=PREFIX,
                # What a listing reports before anything has been measured, and
                # the size the size pass would fill in for it.
                size_bytes=1024 if with_sizes else None,
            )
            for name, version in sorted(self.versions.items())
        )
        if on_listed is not None:
            on_listed(Inventory(environment=PREFIX, backend="pip", rows=rows))

        if self.hold is not None:
            # A held pass stands in for one that is spending minutes on index
            # lookups. It stops the moment it is cancelled - which is what a
            # change finishing underneath it does - and reports what it had
            # already read: the state from before the change.
            while not self.hold.wait(0.02):
                if cancel is not None and cancel.is_set():
                    return Inventory(environment=PREFIX, backend="pip", rows=rows)
        if self.fail_after_listing:
            raise RuntimeError("the package index is unreachable")

        if on_row is not None:
            for row in rows:
                on_row(row)
        return Inventory(environment=PREFIX, backend="pip", rows=rows)

    def metadata_index(self, refresh: bool = False) -> None:
        return None

    def invalidate_metadata(self) -> None:
        self.invalidations += 1

    def backend_needs_elevation(self, action: PackageAction) -> bool:
        return self.needs_elevation

    def backend_can_reach_targets(self, action: PackageAction) -> bool:
        return self.can_reach_targets

    def plan_with_backend(self, name: str, action: PackageAction) -> CommandPlan:
        """The plan the real service builds for a pip fallback, faked."""
        if self.fallback_raises:
            raise BackendError(f"{name} is not available here")

        user = ["--user"] if action.user else []
        return CommandPlan(
            argv=(
                "C:/fake/python.exe",
                "-m",
                name,
                "install",
                "--upgrade",
                *user,
                "--",
                *action.names,
            ),
            action=action,
            label=f"{name} upgrade",
            backend=name,
        )

    def plan(self, action: PackageAction) -> CommandPlan:
        return CommandPlan(
            argv=("pip", "install", "--upgrade", *action.names),
            action=action,
            label="pip upgrade",
            backend="pip",
        )

    def action_for_uninstall_many(self, names) -> PackageAction:
        return PackageAction(kind=ActionKind.UNINSTALL, environment=PREFIX, names=tuple(names))

    def action_for_restore(self, specs) -> PackageAction:
        return PackageAction(kind=ActionKind.INSTALL, environment=PREFIX, specs=tuple(specs))

    def execute(
        self,
        plan: CommandPlan,
        *,
        cancel=None,
        on_line=None,
        elevated: bool = False,
    ) -> CommandResult:
        self.elevated_calls.append(elevated)
        for name in plan.action.names:
            if plan.action.kind is ActionKind.UNINSTALL:
                self.versions.pop(name, None)
            else:
                self.versions[name] = "2.32.0"
        for spec in plan.action.specs:
            name, _, pinned = spec.partition("==")
            self.versions[name] = pinned or "2.32.0"
        return CommandResult(
            argv=plan.argv,
            returncode=0,
            stdout="Successfully installed",
            stderr="",
            duration=0.1,
        )


class FakeProvider:
    """One service, shared by every environment the context asks for."""

    def __init__(self, service: FakeService) -> None:
        self.service = service

    def create(self, prefix: Path) -> FakeService:
        return self.service

    def environments(self) -> tuple[Path, ...]:
        return (PREFIX,)


@pytest.fixture
def service() -> FakeService:
    return FakeService()


@pytest.fixture
def store(tmp_path: Path) -> QSettingsStore:
    return QSettingsStore(path=tmp_path / "settings.ini")


@pytest.fixture
def shell(store: QSettingsStore) -> WindowShell:
    shell = WindowShell(store=store)
    shell.load()
    return shell


@pytest.fixture
def view(
    qtbot,
    qapp: QApplication,
    service: FakeService,
    shell: WindowShell,
    tmp_path: Path,
) -> PackagesView:
    del qapp  # the fixture is only here to guarantee a QApplication exists
    return PackagesView(
        context=UiContext(provider=FakeProvider(service), default_environment=PREFIX),
        shell=shell,
        log_panel=LogPanel(translator=shell.translator),
        snapshots=SnapshotStore(root=tmp_path / "snapshots"),
    )


def _upgrade_request() -> PackageAction:
    return PackageAction(kind=ActionKind.UPGRADE, environment=PREFIX, names=("requests",))


def _installed(view: PackagesView) -> dict[str, str]:
    return {row.name: row.installed for row in view.model.rows}


def test_the_table_is_filled_from_the_listing_before_the_index_answers(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The listing is local; the latest versions behind it are not.

    The table used to stay empty behind a spinner until the enrichment pass had
    finished, which is why a refresh looked like it had hung even when the listing
    had been available for seconds.
    """
    service.hold = threading.Event()
    view.refresh()

    qtbot.waitUntil(lambda: bool(view.model.rows), timeout=5000)
    assert _installed(view) == {"requests": "2.31.0", "urllib3": "2.2.0"}
    # The refresh is still running: what is on screen is the listing, not a result.
    assert view.is_busy

    service.hold.set()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)


def test_a_change_that_finishes_mid_refresh_still_shows_the_new_version(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The regression: the post-change refresh was dropped while another ran.

    The refresh in flight had already read the environment, so it can only report
    the state from before the upgrade. It is therefore cancelled rather than
    waited out - a pass stuck on slow index lookups must not hold the new version
    off the screen - and the fresh pass runs as soon as it stops. Nothing
    releases the hold below: cancelling the stale pass is what unblocks the new
    version.
    """
    service.hold = threading.Event()
    view.refresh()
    qtbot.waitUntil(lambda: len(service.listings) == 1, timeout=5000)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: view._run_task == "", timeout=5000)
    assert service.versions["requests"] == "2.32.0"

    # The stale pass stops itself the moment its cancellation arrives, and the
    # fresh pass lists: the new version is on screen without waiting for the
    # stale pass's slow lookups to run their course.
    qtbot.waitUntil(lambda: len(service.listings) == 2, timeout=5000)
    qtbot.waitUntil(lambda: _installed(view)["requests"] == "2.32.0", timeout=5000)

    service.hold.set()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    assert _installed(view)["requests"] == "2.32.0"
    assert len(service.listings) == 2


def test_refreshes_asked_for_while_one_runs_are_queued_not_dropped(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """Two clicks on Refresh while a pass is held still produce two passes.

    The queue exists for the *user's* refresh: a pass that was asked for while
    another ran must run after it, not vanish. (A change finishing underneath a
    running pass is the stronger case - that one cancels, in the test above.)
    """
    service.hold = threading.Event()
    view.refresh()
    qtbot.waitUntil(lambda: len(service.listings) == 1, timeout=5000)

    view.refresh()
    assert len(service.listings) == 1

    service.hold.set()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    assert len(service.listings) == 2


def test_a_successful_change_invalidates_the_shared_metadata_index(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The index is cached per environment and read by more than this view."""

    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    assert service.invalidations == 0

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: service.invalidations == 1, timeout=5000)

    # One more refresh reads the index again; it must not invalidate it a second time.
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    assert service.invalidations == 1


# -- the two toolbar switches -------------------------------------------------
#
# "Show outdated packages" and "Show package sizes" existed in the settings page
# and in the toolbar, storing a value that nothing ever read: the switches reset
# themselves on every launch and the settings page wrote a preference nobody
# used. They now start in the stored state and write back when they are clicked.


def test_the_switches_start_in_the_stored_state(
    qapp: QApplication, service: FakeService, store: QSettingsStore
) -> None:
    """A preference the settings page writes is a preference the toolbar applies."""
    del qapp
    shell = WindowShell(store=store)
    shell.load()
    shell.apply_settings(
        replace(shell.settings, show_outdated_by_default=False, show_sizes_by_default=False)
    )
    shell.save()

    page = PackagesView(
        context=UiContext(provider=FakeProvider(service), default_environment=PREFIX),
        shell=shell,
        log_panel=LogPanel(translator=shell.translator),
    )

    assert not page.outdated_check.isChecked()
    assert not page.sizes_check.isChecked()
    assert not page.proxy.outdated_only


def test_turning_the_sizes_switch_off_drops_the_measured_sizes(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """A switch that only ever adds something is a one-way door, not a switch."""
    view.refresh()
    qtbot.waitUntil(lambda: bool(view.model.rows) and not view.is_busy, timeout=5000)
    assert all(row.size_bytes is not None for row in view.model.rows)

    view.sizes_check.setChecked(False)
    # The rows stay; only the numbers they were given go away.
    assert view.model.rows
    assert all(row.size_bytes is None for row in view.model.rows)

    view.sizes_check.setChecked(True)
    qtbot.waitUntil(
        lambda: not view.is_busy and all(row.size_bytes is not None for row in view.model.rows),
        timeout=5000,
    )


def test_a_switch_is_stored_for_the_next_launch(
    qapp: QApplication, view: PackagesView, store: QSettingsStore
) -> None:
    del qapp
    view.outdated_check.setChecked(False)

    assert not view.proxy.outdated_only
    reopened = WindowShell(store=store)
    reopened.load()
    assert reopened.settings.show_outdated_by_default is False

    view.sizes_check.setChecked(False)
    reopened = WindowShell(store=store)
    reopened.load()
    assert reopened.settings.show_sizes_by_default is False


def test_a_failed_refresh_does_not_empty_a_table_that_was_listed(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """Listing and index lookups are separate failures, and only one is fatal.

    The listing is local, so it is on screen before the lookups even start; an
    index that cannot be reached must leave that table in place instead of
    replacing it with nothing.
    """
    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    service.fail_after_listing = True
    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    assert _installed(view) == {"requests": "2.31.0", "urllib3": "2.2.0"}


# -- elevation: when uv has nowhere writable to write -------------------------
#
# A backend with no user-site path of its own can only write the environment's
# own site-packages, and on an all-users install that is the one directory a
# normal user may not change. SPEC section 11.5 shapes every test below: the
# request is for one action, it is explained before it is asked for, and it
# goes through the OS's consent mechanism rather than around it.


def _ok_policy() -> ModificationPolicy:
    return ModificationPolicy(
        allowed=True,
        requires_confirmation=False,
        severity=Severity.OK,
        reason_code="ok",
        explanation="this environment may be changed",
    )


def test_an_elevation_needing_action_is_confirmed_then_run_elevated(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The confirmation carries the explanation, the run carries the privileges."""
    service.needs_elevation = True
    # The OS's elevation mechanism is the environment's business, not this
    # test's: the question here is what the *view* does when one is available.
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: True)
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, *, plan, policy, translator, elevation=None, parent=None) -> None:
            recorded["elevation"] = elevation

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: service.elevated_calls == [True], timeout=5000)

    notice = recorded["elevation"]
    assert isinstance(notice, ElevationNotice)
    assert notice.backend == "pip"


def test_elevation_is_refused_before_confirming_when_the_os_cannot_run_it(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    service.needs_elevation = True
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: False)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.elevated_calls == []


def test_an_action_the_backend_cannot_reach_is_offered_to_pip_instead(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The report that produced the feature: uv "succeeded", nothing changed.

    A package the user site shadows cannot be changed by a backend that only
    writes the environment's own site-packages, so the action is not run as
    asked - it is re-planned through pip, which upgrades the shadowing copy
    without administrator privileges, and confirmed with pip's own command.
    The backend is never substituted silently: the dialog is the consent.
    """
    service.can_reach_targets = False
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self, *, plan, policy, translator, elevation=None, fallback=None, parent=None
        ) -> None:
            recorded["plan"] = plan
            recorded["fallback"] = fallback

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: service.elevated_calls == [False], timeout=5000)

    plan = recorded["plan"]
    assert isinstance(plan, CommandPlan)
    assert plan.backend == "pip"
    # The shadowing copy is the one being upgraded, and --user is how the
    # request says so.
    assert "--user" in plan.argv

    notice = recorded["fallback"]
    assert isinstance(notice, FallbackNotice)


def test_declining_the_pip_offer_runs_nothing(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    service.can_reach_targets = False

    class RefusingConfirmation:
        confirmed = False

        def __init__(
            self, *, plan, policy, translator, elevation=None, fallback=None, parent=None
        ) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(packages_module, "ConfirmationDialog", RefusingConfirmation)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.elevated_calls == []


def test_when_pip_cannot_be_resolved_the_error_is_shown_not_hidden(
    view: PackagesView, service: FakeService
) -> None:
    """Offering pip needs pip: its absence is reported instead of a dead offer."""
    service.can_reach_targets = False
    service.fallback_raises = True
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.elevated_calls == []
    assert messages and "pip" in messages[0]


def test_elevation_never_happens_without_an_explicit_confirmation(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The rule holds even when the policy alone would not have asked.

    The fake's policy says ``requires_confirmation=False`` - precisely the
    situation where an implementation might quietly skip the dialog - so the
    elevation request is the only thing standing between the action and a
    silent administrator run. It must stand.
    """
    service.needs_elevation = True

    class RefusingConfirmation:
        confirmed = False

        def __init__(self, *, plan, policy, translator, elevation=None, parent=None) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(packages_module, "ConfirmationDialog", RefusingConfirmation)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.elevated_calls == []


def test_the_confirmation_dialog_explains_what_elevation_means(
    qapp: QApplication, shell: WindowShell
) -> None:
    """Why the command needs the OS's help, and the promise that it is one action."""
    del qapp
    plan = CommandPlan(
        argv=("uv", "pip", "install", "--upgrade", "--", "pip"),
        action=_upgrade_request(),
        label="uv upgrade",
        backend="uv",
    )

    dialog = ConfirmationDialog(
        plan=plan,
        policy=_ok_policy(),
        translator=shell.translator,
        elevation=ElevationNotice(backend="uv"),
    )

    assert dialog.accepts_elevation
    assert not dialog.elevation_label.isHidden()
    assert EN_TABLE[TranslationKey.CONFIRM_ELEVATION_TITLE] in dialog.elevation_text
    assert "uv" in dialog.elevation_text
    assert EN_TABLE[TranslationKey.CONFIRM_ELEVATION_NOTE] in dialog.elevation_text
    assert (
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text()
        == EN_TABLE[TranslationKey.CONFIRM_ELEVATION_BUTTON]
    )


def test_a_plain_confirmation_does_not_mention_elevation(
    qapp: QApplication, shell: WindowShell
) -> None:
    """The notice is an addition, not a permanent fixture of the dialog."""
    del qapp
    plan = CommandPlan(
        argv=("pip", "install", "--upgrade", "--", "requests"),
        action=_upgrade_request(),
        label="pip upgrade",
        backend="pip",
    )

    dialog = ConfirmationDialog(
        plan=plan,
        policy=_ok_policy(),
        translator=shell.translator,
    )

    assert not dialog.accepts_elevation
    assert dialog.elevation_label.isHidden()
    assert not dialog.accepts_fallback
    assert dialog.fallback_label.isHidden()
    assert (
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text()
        == EN_TABLE[TranslationKey.BUTTON_UPGRADE]
    )


def test_the_confirmation_dialog_explains_the_pip_fallback(
    qapp: QApplication, shell: WindowShell
) -> None:
    """Who could not run it, what will instead, and the exact pip command."""
    del qapp
    action = _upgrade_request()
    plan = CommandPlan(
        argv=("python", "-m", "pip", "install", "--user", "--upgrade", "--", "requests"),
        action=action,
        label="pip upgrade",
        backend="pip",
    )

    dialog = ConfirmationDialog(
        plan=plan,
        policy=_ok_policy(),
        translator=shell.translator,
        fallback=FallbackNotice(replaced="uv"),
    )

    assert dialog.accepts_fallback
    assert not dialog.fallback_label.isHidden()
    assert EN_TABLE[TranslationKey.CONFIRM_FALLBACK_TITLE] in dialog.fallback_text
    # The manager that could not run it, and the packages it could not reach.
    assert "uv" in dialog.fallback_text
    assert "requests" in dialog.fallback_text
    assert (
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text()
        == EN_TABLE[TranslationKey.CONFIRM_FALLBACK_BUTTON]
    )


# -- snapshots, undo and the plan queue ----------------------------------------
#
# Every confirmed action records an automatic snapshot first; the History page
# turns it into an undo, and the Snapshots page into a rollback. Both arrive
# here, where the preview shows the exact commands and the queue runs them in
# order.


class AcceptingRollback:
    """A rollback preview the user approved, recording what it showed."""

    confirmed = True

    def __init__(
        self,
        *,
        restore_specs,
        remove_names,
        commands,
        elevated,
        translator,
        parent=None,
    ) -> None:
        self.shown_restore = tuple(restore_specs)
        self.shown_remove = tuple(remove_names)
        self.shown_commands = tuple(commands)

    def exec(self) -> int:
        return QDialog.DialogCode.Accepted


def test_a_confirmed_action_records_an_automatic_snapshot_first(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The undo's raw material: the state as it was *before* the change."""
    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: service.versions["requests"] == "2.32.0", timeout=5000)
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    history = [
        entry for entry in view._context.tasks.history() if entry.meta.get("kind") == "upgrade"
    ]
    assert history and history[-1].meta["names"] == ["requests"]

    snapshot = view._snapshots.load(str(view.environment), str(history[-1].meta["snapshot"]))
    assert snapshot is not None
    assert snapshot.automatic is True
    # The snapshot remembers the pre-upgrade state, not the result.
    assert snapshot.version_of == {"requests": "2.31.0", "urllib3": "2.2.0"}


def test_undo_restores_the_versions_the_action_changed(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    monkeypatch.setattr(packages_module, "RollbackPreviewDialog", AcceptingRollback)

    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    assert _installed(view)["requests"] == "2.32.0"

    recorded = [
        entry for entry in view._context.tasks.history() if entry.meta.get("kind") == "upgrade"
    ][-1]

    view.undo_action(recorded.meta)
    qtbot.waitUntil(lambda: _installed(view)["requests"] == "2.31.0", timeout=5000)

    # The undo was an exact pin, shown as such before it ran.
    assert _installed(view)["requests"] == "2.31.0"


def test_a_full_rollback_removes_new_packages_and_repins_the_rest(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    shown: dict[str, object] = {}

    class RecordingRollback(AcceptingRollback):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)  # type: ignore[arg-type]
            shown["restore"] = self.shown_restore
            shown["remove"] = self.shown_remove
            shown["commands"] = self.shown_commands

    monkeypatch.setattr(packages_module, "RollbackPreviewDialog", RecordingRollback)

    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    snapshot = view._snapshots.capture(
        view.environment,
        [(row.name, row.installed) for row in view._inventory.rows],
    )

    # The environment moves on: requests upgraded, left-pad arrives.
    service.versions["requests"] = "2.32.0"
    service.versions["left-pad"] = "1.0"
    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    view.restore_snapshot(str(view.environment), snapshot.id)
    qtbot.waitUntil(lambda: view._run_task == "" and not view._plan_queue, timeout=5000)
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    # The preview said exactly what would happen, before anything ran.
    assert shown["remove"] == ("left-pad",)
    assert shown["restore"] == ("requests==2.31.0",)

    installed = _installed(view)
    assert installed["requests"] == "2.31.0"
    assert "left-pad" not in installed
    # Two commands, run one after the other, each confirmed once as a batch.
    assert service.elevated_calls[-2:] == [False, False]


def test_undo_without_a_snapshot_says_so(view: PackagesView, service: FakeService) -> None:
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view.undo_action({"kind": "upgrade", "names": ["requests"], "environment": str(PREFIX)})

    assert not view._run_task
    assert service.elevated_calls == []
    assert messages and EN_TABLE[TranslationKey.HISTORY_UNDO_UNAVAILABLE] in messages[0]
