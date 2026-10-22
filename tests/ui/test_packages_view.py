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
from pypackhub.ui.widgets.dialogs import (
    BootstrapNotice,
    ConfirmationDialog,
    ConflictNotice,
    ElevationNotice,
    FallbackNotice,
    TyposquatNotice,
)
from pypackhub.ui.widgets.log_panel import LogPanel, RunOutcome
from pypackhub.ui.widgets.toast import ToastKind

pytestmark = pytest.mark.gui

#: A prefix that never has to exist: the fake service answers from its own dict,
#: so no test in this file touches a real environment or the network.
PREFIX = Path("/fake/env").resolve()


class FakeBackend:
    """The little the view asks of a backend besides listing.

    The capability mirrors pip's honest one, including the size column: a
    backend whose rows can be measured. The one test that needs otherwise
    swaps the capability out.
    """

    capability = BackendCapability(
        name="pip",
        can_install=True,
        can_install_user=False,
        can_show_sizes=True,
    )

    def download_plan(self, action: PackageAction) -> CommandPlan | None:
        return None


class FakeService:
    """The core service, with a dict where the installed versions live."""

    def __init__(self) -> None:
        self.versions = {"requests": "2.31.0", "urllib3": "2.2.0"}
        self.backend = FakeBackend()
        self.listings: list[dict[str, str]] = []
        self.invalidations = 0
        #: The environment label the fake's listings carry. The real listing
        #: names the environment it read; the fake lets a test say which one.
        self.listing_environment: Path = PREFIX
        #: When set, ``refresh`` reports its listing and then waits here, which is
        #: how a refresh is held open while a change happens underneath it.
        self.hold: threading.Event | None = None
        #: When set, ``execute`` waits here before returning, which holds a
        #: change open long enough to observe the run's progress indication.
        self.run_hold: threading.Event | None = None
        #: When set, ``refresh`` fails *after* reporting its listing, which is the
        #: shape of a listing that worked followed by an index that did not.
        self.fail_after_listing = False
        #: Whether the backend needs administrator rights for its actions.
        self.needs_elevation = False
        #: The elevation answer for the action step of a bootstrap batch, the
        #: one the real service asks the not-yet-installed backend for. None
        #: means "answer like a pip-family tool on this environment" (locked
        #: means elevated); a test pins the scoped backends' real answer
        #: (pipx: never) by setting it.
        self.assumed_elevation: bool | None = None
        #: When set, ``execute`` returns this result verbatim - the shape a
        #: real command produced, for the paths that only exist on failure.
        self.run_result: CommandResult | None = None
        #: Lines ``execute`` streams through ``on_line`` before its hold -
        #: the shape a real manager's live output arrives in, percentages
        #: and all.
        self.run_lines: list[str] = []
        #: What ``execute`` was asked for, in order.
        self.elevated_calls: list[bool] = []
        #: When False, the backend cannot change what the interpreter imports
        #: for its targets (the user-site shadowing case).
        self.can_reach_targets = True
        #: The same answer for a uv the bootstrap flow assumes: uv has no
        #: user-site path, so shadowed targets are unreachable through it.
        self.uv_can_reach = True
        #: When set, the pip fallback cannot be planned (no pip on the machine).
        self.fallback_raises = False
        #: What ``required_backend_missing`` reports: empty means "usable".
        self.missing_backend = ""
        #: Whether the forced manager is the right kind for this environment;
        #: False stands for a poetry forced on a plain interpreter.
        self.manager_applies = True
        #: Whether the imported copy of the targets lives in the user site.
        self.targets_user_site = False
        #: The labels of the plans ``execute`` was asked for, in order.
        self.executed: list[str] = []
        #: Whether the environment is writable; drives the bootstrap elevation.
        self.writable = True
        #: Whether ``invalidate_backend_state`` was called.
        self.backend_state_invalidations = 0
        #: What ``typosquat_check`` answers; None means "the name looks fine".
        self.typosquat_warning = None
        #: What ``declared_conflicts`` answers for any (name, version).
        self.conflict_entries: list[object] = []
        #: The specs ``action_for_import`` was last asked to batch-install.
        self.import_specs: tuple[str, ...] = ()

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
        environment = self.listing_environment
        if on_listed is not None:
            on_listed(Inventory(environment=environment, backend="pip", rows=rows))

        if self.hold is not None:
            # A held pass stands in for one that is spending minutes on index
            # lookups. It stops the moment it is cancelled - which is what a
            # change finishing underneath it does - and reports what it had
            # already read: the state from before the change.
            while not self.hold.wait(0.02):
                if cancel is not None and cancel.is_set():
                    return Inventory(environment=environment, backend="pip", rows=rows)
        if self.fail_after_listing:
            raise RuntimeError("the package index is unreachable")

        if on_row is not None:
            for row in rows:
                on_row(row)
        return Inventory(environment=self.listing_environment, backend="pip", rows=rows)

    def metadata_index(self, refresh: bool = False) -> None:
        return None

    def invalidate_metadata(self) -> None:
        self.invalidations += 1

    def backend_needs_elevation(self, action: PackageAction) -> bool:
        return self.needs_elevation

    def elevation_assuming_backend(self, name: str, action: PackageAction) -> bool:
        """The real service asks the backend the batch is about to install;
        the fake answers the way its knobs describe (see ``assumed_elevation``)."""
        del name, action
        if self.assumed_elevation is not None:
            return self.assumed_elevation
        return not self.writable

    def backend_can_reach_targets(self, action: PackageAction, *, backend_name: str = "") -> bool:
        if backend_name == "uv":
            return self.uv_can_reach
        return self.can_reach_targets

    def bootstrap_needs_elevation(self) -> bool:
        return not self.writable

    def invalidate_backend_state(self) -> None:
        self.backend_state_invalidations += 1

    def typosquat_check(self, name: str):
        return self.typosquat_warning

    def declared_conflicts(self, name: str, version: str):
        return tuple(self.conflict_entries)

    def action_for_import(self, specs):
        self.import_specs = tuple(specs)
        return PackageAction(kind=ActionKind.INSTALL, environment=PREFIX, specs=tuple(specs))

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

    def required_backend_missing(self) -> str:
        return self.missing_backend

    def manager_applies_to_environment(self, name: str) -> bool:
        """Whether the forced *name* is the right kind for this environment.

        Pinned by tests to False for the scope-refusal cases; True by
        default because the fake's PREFIX is a stand-in for an environment
        the manager can manage.
        """
        return self.manager_applies

    def action_targets_user_site(self, action: PackageAction) -> bool:
        """Where the imported copy lives, faked; False means the prefix."""
        return self.targets_user_site

    def bootstrap_plans(self, missing: str, *, action: PackageAction) -> list[CommandPlan]:
        """The setup commands, faked: install pip, then the tool itself.

        Mirrors the real service: pip through ensurepip, and any of the
        pip-installable managers (uv, poetry, pipx, pipenv, pdm) as a package
        from the index. conda and mamba raise the same refusal the real
        service does - they are not pip packages, and an install batch for
        them must not exist.
        """
        if missing in ("conda", "mamba"):
            raise BackendError(
                f"{missing} is not a pip package; install it from its own distribution"
            )
        plans = [
            CommandPlan(
                argv=("python", "-m", "ensurepip", "--upgrade"),
                action=action,
                label="install pip",
                backend="pip",
            )
        ]
        if missing in ("uv", "poetry", "pipx", "pipenv", "pdm"):
            plans.append(
                CommandPlan(
                    argv=("python", "-m", "pip", "install", "--upgrade", "--", missing),
                    action=action,
                    label=f"install {missing}",
                    backend="pip",
                )
            )
        return plans

    def plan_assuming_backend(self, name: str, action: PackageAction) -> CommandPlan:
        return CommandPlan(
            argv=(name, "pip", "install", "--upgrade", "--", *action.names),
            action=action,
            label=f"{name} upgrade",
            backend=name,
        )

    def action_for_manager_install(self, name: str) -> PackageAction:
        """The install-only context, faked to match the real service."""
        return PackageAction(kind=ActionKind.INSTALL, environment=PREFIX, specs=(name,))

    def action_for_manager_removal(self, name: str) -> PackageAction:
        """The removal context, faked to match the real service."""
        return PackageAction(kind=ActionKind.UNINSTALL, environment=PREFIX, names=(name,))

    def manager_removal_plan(self, name: str, action: PackageAction) -> CommandPlan:
        """The removal command, faked to match the real service - including
        the refusal for managers pip cannot remove."""
        if name not in ("uv", "poetry", "pipx", "pipenv", "pdm"):
            raise BackendError(
                f"{name} is not a manager this application removes; "
                "only the tools pip installs are removable here"
            )
        return CommandPlan(
            argv=("python", "-m", "pip", "uninstall", "--yes", "--", name),
            action=action,
            label=f"uninstall {name}",
            backend="pip",
        )

    def action_for_uninstall_many(self, names) -> PackageAction:
        return PackageAction(kind=ActionKind.UNINSTALL, environment=PREFIX, names=tuple(names))

    def action_for_upgrade(self, name: str, *, user: bool = False) -> PackageAction:
        return PackageAction(kind=ActionKind.UPGRADE, environment=PREFIX, names=(name,), user=user)

    def action_for_uninstall(self, name: str) -> PackageAction:
        return PackageAction(kind=ActionKind.UNINSTALL, environment=PREFIX, names=(name,))

    def action_for_upgrade_all(self, inventory, *, user: bool = False) -> PackageAction:
        names = tuple(row.name for row in inventory.rows if row.is_outdated)
        return PackageAction(
            kind=ActionKind.UPGRADE_ALL, environment=PREFIX, names=names, user=user
        )

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
        self.executed.append(plan.label)
        if on_line is not None:
            for line in self.run_lines:
                on_line(line)
        if self.run_hold is not None:
            # A held run stands in for a package-manager command that is
            # spending its minutes downloading and installing. It stops the
            # moment it is cancelled, like the real runner does.
            while not self.run_hold.wait(0.02):
                if cancel is not None and cancel.is_set():
                    break
        for name in plan.action.names:
            if plan.action.kind is ActionKind.UNINSTALL:
                self.versions.pop(name, None)
            else:
                self.versions[name] = "2.32.0"
        for spec in plan.action.specs:
            name, _, pinned = spec.partition("==")
            self.versions[name] = pinned or "2.32.0"
        if self.run_result is not None:
            return self.run_result
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


def test_the_run_bar_fills_with_the_work_the_manager_reports(view: PackagesView) -> None:
    """68% of the run means 68% of the bar - never a spinner, never a guess.

    The bar is determinate from a run's first frame and fills only when a
    real event justifies it: packages resolved, files fetched, the install
    phase, success. pip's per-file percentage - the same number the log
    shows - refines the file in flight, a redraw that goes backwards never
    drags the bar with it, and the next queued command starts its own count
    from zero.
    """
    view._show_run_progress("pip upgrade")
    assert (view._run_progress.minimum(), view._run_progress.maximum()) == (0, 100)
    assert view._run_progress.value() == 0

    view._on_run_line("Collecting requests==2.32.3")
    view._on_run_line("  Downloading requests-2.32.3-py3-none-any.whl (64 kB)")
    assert view._run_progress.value() == 0  # the fetch started; nothing finished

    # 68% of the one file the run has to fetch is 68% of the work span.
    view._on_run_line("     \u2501\u2501\u2501\u2501  68%  3.5/5.1 MB")
    assert view._run_progress.value() == 54

    # A redraw that claims less than before never moves the bar backwards.
    view._on_run_line("     \u2501  3%  0.2/5.1 MB")
    assert view._run_progress.value() == 54

    # A second fetch closes the first file. The "%2B" in the wheel's name is
    # URL encoding, not a percentage - the line counts as the fetch it
    # announces, never as a redraw of the one before it.
    view._on_run_line("Downloading pkg-2.0%2B1-py3-none-any.whl (64 kB)")
    assert view._run_progress.value() == 79  # first file done, cap one shy of the phase line

    view._on_run_line("Installing collected packages: requests")
    assert view._run_progress.value() == 80

    view._on_run_line("Successfully installed requests-2.32.3")
    assert view._run_progress.value() == 100

    # The next queued plan starts its own count from zero again.
    view._show_run_progress("uv upgrade")
    assert view._run_progress.value() == 0


def test_a_venv_creation_keeps_the_animated_bar(view: PackagesView) -> None:
    """A tool that prints nothing countable gets the honest spinner.

    Creating a virtual environment streams no per-file events; a determinate
    bar would sit at zero for the whole run, insisting nothing was
    happening. The animated bar is the truthful answer there.
    """
    view._show_run_progress("create virtual environment", determinate=False)
    assert view._run_progress.maximum() == 0  # min == max: Qt's busy mode

    view._on_run_line("creating the environment")
    assert view._run_progress.maximum() == 0  # no line flipped it determinate


def test_a_running_change_shows_the_progress_bar_until_it_ends(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The one sign of life a change gives with the log panel closed.

    pip and uv stream lines but report no percentage a GUI could trust, so
    the bar is the animated kind: it appears with the run, carries the
    command's own label, and disappears when the run does - success,
    failure and cancellation alike all end it the same way. (``isHidden``
    rather than ``isVisible``: the view itself is never shown in tests.)
    """
    service.run_hold = threading.Event()

    assert view._run_progress.isHidden()
    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: not view._run_progress.isHidden(), timeout=5000)
    assert view._run_progress.format() == "pip upgrade"

    service.run_hold.set()
    qtbot.waitUntil(lambda: view._run_progress.isHidden(), timeout=5000)
    assert not view._run_task


def test_the_progress_bar_spans_a_queued_pair_of_plans(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """A restore is one decision that can be two commands.

    The bar must not blink away between them: the user approved a single
    restore, and the batch is still running until the second plan ends.
    """
    service.run_hold = threading.Event()
    plans = [
        (service.plan(_upgrade_request()), False),
        (
            service.plan(
                PackageAction(kind=ActionKind.INSTALL, environment=PREFIX, specs=("other==1.0",))
            ),
            False,
        ),
    ]

    view.run_confirmed_plans(plans)
    qtbot.waitUntil(lambda: not view._run_progress.isHidden(), timeout=5000)

    service.run_hold.set()
    qtbot.waitUntil(lambda: not view._run_task and not view._plan_queue, timeout=5000)
    qtbot.waitUntil(lambda: view._run_progress.isHidden(), timeout=5000)
    assert service.versions["other"] == "1.0"


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


def test_a_backend_whose_rows_cannot_be_sized_hides_the_switch(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """pipx's applications live in their own venvs, so the size column -
    which measures the selected environment - could put another
    environment's number on a pipx row. Unknown is honest; a wrong number is
    not, so the switch is hidden and no sizes pass runs."""
    service.backend.capability = BackendCapability(
        name="pipx",
        can_install=True,
        can_show_sizes=False,
    )

    view.set_environment(PREFIX)

    assert view.sizes_check.isHidden()
    # and even the saved default cannot start a sizes pass through a
    # switch nobody can see
    assert view._sizes_available() is False


def test_a_sizes_capable_backend_keeps_the_switch(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """pip, uv and conda rows measure or read "unknown" - both honest - so
    the switch stays where it always was for them."""
    del service
    view.set_environment(PREFIX)

    assert not view.sizes_check.isHidden()


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

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
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


def test_a_missing_manager_is_installed_before_the_action_runs(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The report behind the feature: with uv chosen and not installed, the
    action used to be an error (or a silent pip substitution).

    Now the tool the user chose is installed first - pip through ensurepip,
    uv as a package - and the action's own command follows through that tool.
    One confirmation shows every command in the batch, so the approval covers
    the whole sequence (SPEC section 11.7), and the queue runs them in order:
    the action only starts once the manager it asked for exists.
    """
    service.missing_backend = "uv"
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["plan"] = plan
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    notice = recorded["bootstrap"]
    assert isinstance(notice, BootstrapNotice)
    assert notice.backend == "uv"
    assert len(notice.commands) == 2  # ensurepip, then pip install uv

    qtbot.waitUntil(
        lambda: service.executed == ["install pip", "install uv", "uv upgrade"],
        timeout=5000,
    )
    qtbot.waitUntil(lambda: view._run_progress.isHidden(), timeout=5000)
    assert service.versions["requests"] == "2.32.0"


def test_a_missing_pipx_is_installed_before_the_upgrade_runs(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The user's report, made concrete: pipx is forced in the settings and
    not installed, and the upgrade asks instead of failing - "pipx is not
    installed; install it and continue?" - then installs pipx first and runs
    the upgrade through it. One confirmation covers the whole batch (SPEC
    section 11.7), and the queue guarantees the order: the upgrade only
    starts once the manager it asked for exists.
    """
    service.missing_backend = "pipx"
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["plan"] = plan
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    notice = recorded["bootstrap"]
    assert isinstance(notice, BootstrapNotice)
    assert notice.backend == "pipx"
    assert len(notice.commands) == 2  # ensurepip, then pip install pipx

    # And the reachability question did not divert the batch to a pip
    # fallback: the fake answers like the real service for a pipx that never
    # touches the selected environment.
    qtbot.waitUntil(
        lambda: service.executed == ["install pip", "install pipx", "pipx upgrade"],
        timeout=5000,
    )


def test_a_missing_pipx_batch_runs_only_its_install_steps_elevated(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The report behind the rule: pipx was forced on a locked all-users
    environment, and the upgrade ran elevated with the installation that
    preceded it - the consent boundary carries no pipes, so the failure
    printed as "Failed:" with nothing after it. The install steps write the
    environment and keep the OS's help; the upgrade writes pipx's own home,
    runs as the normal user, and its output streams - including its errors.
    """
    service.missing_backend = "pipx"
    service.writable = False
    service.assumed_elevation = False
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: True)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: service.elevated_calls == [True, True, False], timeout=5000)
    assert service.executed == ["install pip", "install pipx", "pipx upgrade"]


def test_a_silent_failure_says_it_captured_nothing(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """ "Failed:" with nothing after it reads as the application's own bug.

    A command that dies without producing a byte gets that said plainly in
    the log and the status line, instead of an empty interpolation.
    """
    service.run_result = CommandResult(
        argv=("pip", "upgrade", "requests"),
        returncode=1,
        stdout="",
        stderr="",
        duration=0.1,
    )
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: bool(messages) and view._run_task == "", timeout=5000)
    # The sentence was on the status line; the refresh that follows a failed
    # run may trail it with its own messages, so membership - not position -
    # is what the run actually promised.
    assert EN_TABLE[TranslationKey.LOG_FAILED_SILENT] in messages


def test_an_elevated_failure_explains_its_missing_output(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The consent boundary carries no pipes: an elevated run that fails has
    no output to show, and the log says *why* it is silent rather than
    pretending the failure had nothing to say."""
    service.needs_elevation = True
    service.run_result = CommandResult(
        argv=("uv", "pip", "install", "--upgrade", "requests"),
        returncode=1,
        stdout="",
        stderr="",
        duration=0.1,
    )
    messages: list[str] = []
    view.status_changed.connect(messages.append)
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: True)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: bool(messages) and view._run_task == "", timeout=5000)
    assert EN_TABLE[TranslationKey.LOG_FAILED_ELEVATED] in messages
    assert service.elevated_calls == [True]


def test_the_elevation_refusal_names_the_missing_manager(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """pipx was forced and not installed, the environment is locked, and the
    OS cannot elevate: the refusal names *pipx* - the manager the offer was
    about to install - not whatever backend the service would have fallen
    back to, and not an exception from resolving one that does not exist."""
    service.missing_backend = "pipx"
    service.writable = False
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: False)
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    assert not view.confirm_and_run(_upgrade_request())
    assert messages and "pipx" in messages[0]
    assert service.executed == []


# -- installing a manager from the settings page ----------------------------------


def test_a_manager_install_from_settings_runs_the_batch_and_invalidates(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The front of the story the user asked for: pick pipx in Settings, say
    "install it now", and the install runs through the same confirm-and-run
    path as every change - one dialog with the exact commands, the batch in
    order - and the service re-probes afterwards so the next upgrade finds
    pipx instead of a cached "missing"."""
    recorded: dict[str, object] = {}
    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["plan"] = plan
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    view.install_manager("pipx")

    notice = recorded["bootstrap"]
    assert isinstance(notice, BootstrapNotice)
    assert notice.backend == "pipx"
    assert notice.install_only is True
    # The installing command is the plan the dialog was built around; the
    # ensurepip step rides in the notice.
    assert recorded["plan"].label == "install pipx"
    assert notice.commands == ("python -m ensurepip --upgrade",)

    # The immediate answer to "did it start?", before any output exists.
    started_toast = EN_TABLE[TranslationKey.LOG_INSTALLING_MANAGER].replace("%(backend)s", "pipx")
    assert (started_toast, ToastKind.INFO) in toasts

    qtbot.waitUntil(lambda: service.executed == ["install pip", "install pipx"], timeout=5000)
    assert service.backend_state_invalidations == 1


def test_the_settings_row_bar_follows_the_install_the_run_reports(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The manager row's run state, from the run's own numbers: the batch
    starts it counting (an install's output carries percentages), every
    percentage its lines report moves it, and the outcome ends it."""
    service.run_hold = threading.Event()
    service.run_lines = ["Collecting pipx", "Downloading pipx-1.7.0", "  61% ..."]
    started: list[bool] = []
    percents: list[int] = []
    finished: list[object] = []
    view.manager_run_started.connect(started.append)
    view.manager_progress.connect(percents.append)
    view.manager_run_finished.connect(finished.append)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    view.install_manager("pipx")
    try:
        qtbot.waitUntil(lambda: bool(started), timeout=5000)
    finally:
        service.run_hold.set()

    qtbot.waitUntil(lambda: len(finished) == 1, timeout=5000)
    # The batch is two commands (ensurepip, then the install itself), and
    # each step restarts the row's bar - two starts, one outcome.
    assert started == [True, True]
    # 61% of one file of one package is 61% of the run's work span: the
    # overall number the tracker justifies from the run's own lines.
    assert percents == [48, 48]
    assert finished == [RunOutcome.DONE]


def test_a_failed_manager_install_ends_the_row_bar_with_the_failure(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """A manager install that fails reports the failure through the row's
    outcome, so the settings page can turn its bar red and point at the
    log and History for the reason."""
    service.run_hold = threading.Event()
    service.run_result = CommandResult(
        argv=("python", "-m", "pip", "install", "pipx"),
        returncode=1,
        stdout="",
        stderr="no network",
        duration=0.1,
    )
    finished: list[object] = []
    view.manager_run_finished.connect(finished.append)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    view.install_manager("pipx")
    try:
        qtbot.waitUntil(lambda: bool(service.executed), timeout=5000)
    finally:
        service.run_hold.set()

    qtbot.waitUntil(lambda: len(finished) == 1, timeout=5000)
    assert finished == [RunOutcome.FAILED]


def test_a_manager_removal_takes_the_bar_that_only_animates(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """An uninstall prints nothing countable, so the row's bar can only
    animate - the busy shape is the truthful one for it, and the run still
    reports its outcome when it ends."""
    service.run_hold = threading.Event()
    started: list[bool] = []
    finished: list[object] = []
    view.manager_run_started.connect(started.append)
    view.manager_run_finished.connect(finished.append)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    view.remove_manager("pipx")
    try:
        qtbot.waitUntil(lambda: bool(started), timeout=5000)
    finally:
        service.run_hold.set()

    qtbot.waitUntil(lambda: len(finished) == 1, timeout=5000)
    assert started == [False]
    assert finished == [RunOutcome.DONE]


def test_a_manager_install_refused_in_the_dialog_runs_nothing(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    class RefusingConfirmation:
        confirmed = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(packages_module, "ConfirmationDialog", RefusingConfirmation)

    view.install_manager("pipx")

    assert service.executed == []
    assert service.backend_state_invalidations == 0


def test_a_manager_that_pip_cannot_install_is_said_not_started(
    view: PackagesView, service: FakeService
) -> None:
    """conda is not a pip package; the settings-page offer refuses with the
    same sentence the action-time flow does, and nothing runs."""
    messages: list[str] = []
    view.status_changed.connect(messages.append)
    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))

    view.install_manager("conda")

    assert messages and "not a pip package" in messages[0]
    assert toasts and toasts[0][1] is ToastKind.ERROR
    assert service.executed == []


def test_a_manager_install_into_a_locked_environment_needs_the_os_or_is_refused(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    service.writable = False
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: False)
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view.install_manager("pipx")

    assert messages and "pipx" in messages[0]
    assert service.executed == []


# -- removing a manager from the settings page -------------------------------------


def test_a_manager_removal_runs_the_command_and_invalidates(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The mirror of the install: one command, pip taking back out what it
    put in, confirmed with its exact argv and recorded like every other
    uninstall - and the service re-probes afterwards so the status answers
    honestly on the next visit."""
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["plan"] = plan

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    view.remove_manager("pipx")

    assert recorded["plan"].label == "uninstall pipx"

    qtbot.waitUntil(lambda: service.executed == ["uninstall pipx"], timeout=5000)
    assert service.backend_state_invalidations == 1


def test_a_manager_removal_refused_in_the_dialog_runs_nothing(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    class RefusingConfirmation:
        confirmed = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(packages_module, "ConfirmationDialog", RefusingConfirmation)

    view.remove_manager("pipx")

    assert service.executed == []
    assert service.backend_state_invalidations == 0


def test_a_manager_pip_cannot_remove_is_said_not_started(
    view: PackagesView, service: FakeService
) -> None:
    """conda was not installed by pip, so pip has nothing to say about
    removing it: the same refusal shape as the install, and nothing runs."""
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view.remove_manager("conda")

    assert messages and "not a manager this application removes" in messages[0]
    assert service.executed == []


def test_a_manager_removal_into_a_locked_environment_needs_the_os_or_is_refused(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    service.writable = False
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: False)
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view.remove_manager("pipx")

    assert messages and "pipx" in messages[0]
    assert service.executed == []


def test_an_elevated_run_gets_the_determinate_bar_because_its_output_arrives(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The report this reverses: an install into a locked environment ran
    elevated, the consent boundary carried no pipes, no countable event
    could arrive, and the bar sat at zero - which reads as "nothing is
    happening" while the OS's own prompt is up. The elevated half is now
    a helper that redirects the command's output to a file this process
    follows, so the run's real percentages arrive like any other run's
    and the counting bar is the honest one again."""
    service.needs_elevation = True
    service.run_hold = threading.Event()
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: True)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    try:
        qtbot.waitUntil(lambda: view._run_progress.maximum() == 100, timeout=5000)
    finally:
        service.run_hold.set()

    qtbot.waitUntil(lambda: view._run_progress.isHidden(), timeout=5000)


def test_a_plain_run_shows_the_determinate_bar_the_manager_feeds(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The contrast: a run whose manager streams its work gets the real
    percentage bar, filled by the events its own output reports."""
    service.run_hold = threading.Event()

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    try:
        qtbot.waitUntil(lambda: view._run_progress.maximum() == 100, timeout=5000)
    finally:
        service.run_hold.set()


def test_a_bootstrap_into_a_locked_environment_runs_elevated_or_is_refused(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """Installing a manager writes to the environment: locked means elevated.

    Without the elevation path the batch would queue a doomed command -
    pip cannot write a locked site-packages as a normal user, and a uv that
    landed anywhere but the environment would never be found by the plan's
    own first word. So the batch either runs with the OS's help or is
    refused before anything starts, exactly like any other action.
    """
    service.missing_backend = "uv"
    service.writable = False
    monkeypatch.setattr(packages_module, "elevation_supported", lambda: False)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.executed == []

    monkeypatch.setattr(packages_module, "elevation_supported", lambda: True)

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["elevation"] = elevation
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    recorded: dict[str, object] = {}
    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    assert isinstance(recorded["elevation"], ElevationNotice)
    assert isinstance(recorded["bootstrap"], BootstrapNotice)
    qtbot.waitUntil(lambda: len(service.elevated_calls) == 3, timeout=5000)
    assert service.elevated_calls == [True, True, True]


def test_a_missing_uv_that_cannot_reach_the_target_falls_back_to_pip(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The bootstrap honours the reachability rule, against uv's own semantics.

    A uv that cannot change the package the interpreter imports would
    "succeed" and change nothing - the exact defect the reachability check
    exists for - so the shadowing copy is offered to pip instead, exactly as
    it is when uv is installed.
    """
    service.missing_backend = "uv"
    service.uv_can_reach = False
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["fallback"] = fallback
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    assert isinstance(recorded["fallback"], FallbackNotice)
    assert recorded["bootstrap"] is None
    qtbot.waitUntil(lambda: service.executed == ["pip upgrade"], timeout=5000)


def test_an_installed_name_the_validator_refuses_is_reported_not_raised(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """One odd row must not break the toolbar for the whole view.

    The action builders re-validate names; the install and downgrade paths
    already caught that, but an exception out of the upgrade click handler
    is a crash where every sibling path shows a toast.
    """
    from pypackhub.core.validation import Reason, ValidationError

    view.refresh()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)
    view.table.selectRow(0)

    def refusing_upgrade(name: str, *, user: bool = False) -> PackageAction:
        raise ValidationError(Reason.INVALID_NAME, "not a valid name", value=name)

    monkeypatch.setattr(service, "action_for_upgrade", refusing_upgrade)
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view.upgrade_button.click()

    assert not view._run_task
    assert messages


def test_a_refresh_that_outlives_an_environment_switch_is_not_applied(
    qtbot, view: PackagesView, service: FakeService
) -> None:
    """The answer of one environment must never paint another.

    A pass that listed environment A finishes after the user switched to B;
    without the identity checks it repainted A's rows under B's header. The
    fake labels its listings with the environment it is asked about, so the
    two passes are distinguishable by more than their content.
    """
    service.hold = threading.Event()
    other = PREFIX.parent / "other-env"
    view.refresh()
    qtbot.waitUntil(lambda: len(service.listings) == 1, timeout=5000)

    # From here the fake lists as the *new* environment; the pass already
    # running captured A's label at its start.
    service.listing_environment = other
    view.set_environment(other)

    service.hold.set()
    qtbot.waitUntil(lambda: not view.is_busy, timeout=5000)

    # What is on screen is the new environment's answer. Had the stale pass
    # been applied, its listing - labelled with the old environment - would
    # be the one in the view instead.
    assert view._inventory is not None
    assert view._inventory.environment == other


def test_the_bootstrap_invalidates_what_was_probed_about_the_managers(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """After the batch installs a manager, the next probe must not be stale."""
    service.missing_backend = "uv"

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    qtbot.waitUntil(lambda: view._run_progress.isHidden(), timeout=5000)
    assert service.backend_state_invalidations >= 1


def test_the_bootstrap_offer_announces_the_install_when_it_starts(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """Whichever door the install came through - this offer, or the settings
    page's question - the first word about it is the same: a toast the
    instant the batch begins, before any output exists to show."""
    service.missing_backend = "uv"
    toasts: list[tuple[str, ToastKind]] = []
    view.toast.connect(lambda message, kind: toasts.append((message, kind)))

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())
    started_toast = EN_TABLE[TranslationKey.LOG_INSTALLING_MANAGER].replace("%(backend)s", "uv")
    qtbot.waitUntil(lambda: (started_toast, ToastKind.INFO) in toasts, timeout=5000)


def test_declining_the_bootstrap_offer_installs_nothing(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    service.missing_backend = "uv"

    class RefusingConfirmation:
        confirmed = False

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(packages_module, "ConfirmationDialog", RefusingConfirmation)

    assert not view.confirm_and_run(_upgrade_request())
    assert not view._run_task
    assert service.executed == []


def test_the_confirmation_dialog_explains_what_the_bootstrap_does(
    qapp: QApplication, shell: WindowShell
) -> None:
    """The tool's absence, what installs it, and that the batch is one approval."""
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
        bootstrap=BootstrapNotice(backend="uv", commands=("python -m ensurepip --upgrade",)),
    )

    assert dialog.accepts_bootstrap
    assert not dialog.bootstrap_label.isHidden()
    assert EN_TABLE[TranslationKey.CONFIRM_BOOTSTRAP_TITLE] in dialog.bootstrap_text
    assert "uv" in dialog.bootstrap_text
    # The preview carries the installing commands above the action's own one.
    assert "python -m ensurepip --upgrade" in dialog.command_text
    assert "uv pip install --upgrade -- pip" in dialog.command_text
    assert (
        dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text()
        == EN_TABLE[TranslationKey.CONFIRM_BOOTSTRAP_BUTTON]
    )


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
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            typosquat=None,
            conflicts=None,
            parent=None,
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
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            typosquat=None,
            conflicts=None,
            parent=None,
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

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
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


def test_the_confirmation_dialog_says_when_the_manager_cannot_manage_the_environment(
    qapp: QApplication, shell: WindowShell
) -> None:
    """The report: poetry was forced in the settings, and the upgrades said
    only "run with pip" - the reason shown was about user-site shadowing,
    which was not even true here. A manager out of its scope gets the
    sentence that is: poetry manages project folders, and this environment
    is not one."""
    del qapp
    action = _upgrade_request()
    plan = CommandPlan(
        argv=("python", "-m", "pip", "install", "--upgrade", "--", "requests"),
        action=action,
        label="pip upgrade",
        backend="pip",
    )

    dialog = ConfirmationDialog(
        plan=plan,
        policy=_ok_policy(),
        translator=shell.translator,
        fallback=FallbackNotice(replaced="poetry", scope="project"),
    )

    assert (
        EN_TABLE[TranslationKey.CONFIRM_FALLBACK_SCOPE_PROJECT].replace("%(backend)s", "poetry")
        in dialog.fallback_text
    )
    # The scope sentence says what to do about it, not only what went wrong.
    assert "project folder" in dialog.fallback_text


def test_a_forced_poetry_that_cannot_manage_this_environment_falls_back_with_the_true_reason(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The reported dead-end: poetry was set in the settings, and upgrading
    any package in the plain interpreter offered only pip - with the
    user-site sentence, or an offer to install the poetry that was already
    installed. The scope refusal is its own path now: the notice says why
    poetry cannot manage this environment, and pip runs the action with
    its own exact command."""
    service.missing_backend = "poetry"
    service.manager_applies = False
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["fallback"] = fallback
            recorded["argv"] = plan.argv

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())

    fallback = recorded["fallback"]
    assert isinstance(fallback, FallbackNotice)
    assert fallback.replaced == "poetry"
    assert fallback.scope == "project"
    # The imported copy lives in the prefix here, so pip's plan goes
    # without --user: the flag would shadow the copy instead of changing it.
    assert "--user" not in recorded["argv"]
    qtbot.waitUntil(lambda: bool(service.executed), timeout=5000)


def test_a_scope_fallback_keeps_the_user_flag_when_the_copy_lives_in_the_user_site(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The same forced poetry, a package whose imported copy lives in the
    user site: pip's plan carries --user, because that is where the copy
    the interpreter sees can actually be changed."""
    service.missing_backend = "poetry"
    service.manager_applies = False
    service.targets_user_site = True
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    original_plan_with_backend = service.plan_with_backend

    def recording_plan_with_backend(name, action):
        plan = original_plan_with_backend(name, action)
        recorded["argv"] = plan.argv
        return plan

    monkeypatch.setattr(service, "plan_with_backend", recording_plan_with_backend)

    assert view.confirm_and_run(_upgrade_request())

    assert "--user" in recorded["argv"]
    qtbot.waitUntil(lambda: bool(service.executed), timeout=5000)


def test_a_manager_that_does_apply_here_still_gets_the_bootstrap_offer(
    qtbot, view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The scope refusal did not eat the honest bootstrap: a missing
    manager that *can* manage this environment - a poetry missing on a
    registered poetry project - is still installed first, then the action
    runs through it."""
    service.missing_backend = "poetry"
    service.manager_applies = True
    recorded: dict[str, object] = {}

    class AcceptingConfirmation:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["bootstrap"] = bootstrap

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(packages_module, "ConfirmationDialog", AcceptingConfirmation)

    assert view.confirm_and_run(_upgrade_request())

    assert isinstance(recorded["bootstrap"], BootstrapNotice)


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


# -- the supply-chain and requirements phase -------------------------------------


def _install_request() -> PackageAction:
    return PackageAction(kind=ActionKind.INSTALL, environment=PREFIX, specs=("requests==2.31.0",))


def _recording_confirmation(recorded: dict[str, object]) -> type:
    """A confirmation fake that records every notice it was handed."""

    class Accepting:
        confirmed = True

        def __init__(
            self,
            *,
            plan,
            policy,
            translator,
            elevation=None,
            fallback=None,
            bootstrap=None,
            typosquat=None,
            conflicts=None,
            parent=None,
        ) -> None:
            recorded["typosquat"] = typosquat
            recorded["conflicts"] = conflicts
            recorded["plan"] = plan

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    return Accepting


def test_a_typosquatted_install_warns_in_the_confirmation(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The name is one letter away from a package everybody installs: the
    confirmation says so before the button next to the warning is pressed."""
    from pypackhub.core.trust import TyposquatWarning

    service.typosquat_warning = TyposquatWarning(name="reqeusts", suspect_of="requests")
    recorded: dict[str, object] = {}
    monkeypatch.setattr(packages_module, "ConfirmationDialog", _recording_confirmation(recorded))

    assert view.confirm_and_run(_install_request())
    warning = recorded["typosquat"]
    assert isinstance(warning, TyposquatNotice)
    assert warning.warning.suspect_of == "requests"


def test_a_clean_install_carries_no_supply_chain_warnings(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """Confirmations are off in the fake policy, so a clean name forces
    nothing: no dialog is manufactured, no warning is invented."""
    recorded: dict[str, object] = {}
    monkeypatch.setattr(packages_module, "ConfirmationDialog", _recording_confirmation(recorded))

    assert view.confirm_and_run(_install_request())
    assert not recorded


def test_a_pinned_downgrade_warns_about_the_ranges_it_breaks(
    view: PackagesView, service: FakeService, monkeypatch
) -> None:
    """The declared constraints of installed dependents, before the break."""
    from pypackhub.core.deps import RequirementConflict

    service.conflict_entries = [
        RequirementConflict(
            package="httpie",
            requirement="requests>=2.28",
            target="requests",
            version="2.0.0",
        )
    ]
    recorded: dict[str, object] = {}
    monkeypatch.setattr(packages_module, "ConfirmationDialog", _recording_confirmation(recorded))

    action = PackageAction(
        kind=ActionKind.DOWNGRADE, environment=PREFIX, names=("requests",), version="2.0.0"
    )
    assert view.confirm_and_run(action)

    conflicts = recorded["conflicts"]
    assert isinstance(conflicts, ConflictNotice)
    assert conflicts.entries[0].package == "httpie"


def test_import_reads_reviews_and_installs_the_chosen_lines(
    qtbot, view: PackagesView, service: FakeService, monkeypatch, tmp_path: Path
) -> None:
    """An untrusted file goes through the parser, the review and one batch.

    The lines the parser refused cannot be selected back in; the lines it
    flagged start unselected; what the user ratifies reaches the one
    confirm-and-run path as a single install.
    """
    target = tmp_path / "requirements.txt"
    target.write_text(
        "\n".join(
            (
                "requests==2.31.0",
                "# just a comment",
                "--index-url https://mirror.example/simple",
                "urllib3==2.2.0",
                "not a valid spec!!",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        packages_module.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )

    reviewed: dict[str, object] = {}

    class AcceptingPreview:
        def __init__(self, *, parsed, translator, parent=None) -> None:
            reviewed["parsed"] = parsed

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

        def selected_specs(self) -> tuple[str, ...]:
            return ("requests==2.31.0",)

    monkeypatch.setattr(packages_module, "ImportPreviewDialog", AcceptingPreview)
    monkeypatch.setattr(packages_module, "ConfirmationDialog", _recording_confirmation({}))

    view._import_requested()
    qtbot.waitUntil(lambda: bool(view._run_task), timeout=5000)

    parsed = reviewed["parsed"]
    assert parsed.review, "the option line must reach the review"
    assert parsed.invalid, "the garbage line must be refused"
    assert service.import_specs == ("requests==2.31.0",)


def test_export_writes_a_sorted_pinned_file(
    view: PackagesView, service: FakeService, monkeypatch, tmp_path: Path
) -> None:
    """What is installed, pinned, sorted - and nothing that cannot install."""
    view._inventory = Inventory(
        environment=PREFIX,
        backend="pip",
        rows=(
            PackageRow(name="urllib3", installed="2.2.0"),
            PackageRow(name="requests", installed="2.31.0"),
            PackageRow(name="broken", installed=""),
        ),
    )
    target = tmp_path / "requirements.txt"
    monkeypatch.setattr(
        packages_module.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(target), "")),
    )
    messages: list[str] = []
    view.status_changed.connect(messages.append)

    view._export_requested()

    assert target.read_text(encoding="utf-8") == "requests==2.31.0\nurllib3==2.2.0\n"
    # The raw table entry is the template; the message is it, interpolated.
    assert messages and messages[0] == EN_TABLE[TranslationKey.PACKAGES_EXPORT_WRITTEN].replace(
        "%(path)s", str(target)
    )


# -- layout ----------------------------------------------------------------------


def test_no_toolbar_button_is_ever_squeezed_below_its_text(qtbot, view: PackagesView) -> None:
    """The toolbar's own lesson: one row of eight buttons clipped half of them.

    The single row's minimum width was 1700+ pixels - wider than the window
    at any size it allows - so the layout squeezed buttons to ~86px and their
    labels ended mid-word. The two-row toolbar fits the packages pane at the
    window's stated minimum with room to spare, and this test keeps it that
    way: a future control that makes a row overflow again fails here instead
    of shipping clipped text.
    """
    qtbot.addWidget(view)
    # 740px is the packages pane's width at the 960px window minimum, with
    # the default sidebar: the tightest width the application promises to
    # lay out correctly.
    view.resize(740, 600)
    view.show()
    qtbot.waitUntil(lambda: view.install_button.width() > 0, timeout=5000)

    from PySide6.QtWidgets import QPushButton

    buttons = [
        widget
        for widget in (
            view.refresh_button,
            view.export_button,
            view.import_button,
            view.install_button,
            view.upgrade_button,
            view.downgrade_button,
            view.uninstall_button,
            view.upgrade_all_button,
        )
        if isinstance(widget, QPushButton)
    ]
    for button in buttons:
        needed = button.minimumSizeHint().width()
        assert button.width() >= needed - 2, (
            f"{button.text()!r} is squeezed to {button.width()}px "
            f"(needs {needed}px) - the toolbar row overflows again"
        )
