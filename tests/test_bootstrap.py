"""The missing-manager bootstrap: what "automatic" and a forced choice do.

Two behaviours, both from user reports:

* with the automatic choice a machine without uv - and an environment without
  a pip module, which is what a uv-created virtual environment looks like -
  must quietly degrade to what works instead of reporting an error, and when
  nothing works at all the user is offered to set pip up rather than refused;
* a manager forced in the settings is honoured by installing *it* first and
  then running the action through it - never by silently substituting
  another backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pypackhub.core.inventory as inventory_module
from pypackhub.core.backends.base import (
    ActionKind,
    BackendError,
    PackageAction,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.uv import uv_path_in_prefix
from pypackhub.core.inventory import InventoryService
from pypackhub.core.platform_guard import EnvironmentKind, InterpreterFacts

PREFIX = Path("/fake/env").resolve()
PYTHON = PREFIX / "Scripts" / "python.exe"


def service_facts() -> InterpreterFacts:
    """The facts every service in this file is pinned to."""
    return InterpreterFacts(
        prefix=PREFIX,
        executable=PYTHON,
        kind=EnvironmentKind.VENV,
        externally_managed=False,
        externally_managed_marker=None,
        is_alias_stub=False,
    )


class _UnavailableBackend:
    """Stands in for a manager that is installed but cannot even list."""

    def executable_path(self) -> Path:
        return PREFIX / "definitely-not-here"

    def list_packages(self, environment: Path, *, facts: object = None, cancel=None):
        raise BackendError(f"{PYTHON}: No module named pip")


@pytest.fixture
def service() -> InventoryService:
    service = InventoryService(prefix=PREFIX)
    service._facts = service_facts()
    return service


def _upgrade() -> PackageAction:
    return PackageAction(kind=ActionKind.UPGRADE, environment=PREFIX, names=("requests",))


# -- the automatic choice never turns absence into an error ---------------------


def test_a_listing_treats_a_missing_manager_as_absence_not_failure(
    monkeypatch, service: InventoryService
) -> None:
    """The report: 'automatic' on a machine without uv showed a pip error.

    The environment had no pip module (uv-created virtual environments ship
    without one), so the pip listing failed with "No module named pip" and
    the packages view presented that as the environment's error. A manager
    that is missing is an absence, not a failure: nothing is reported, and
    whatever else can list does.
    """
    monkeypatch.setattr(
        inventory_module, "_make_backend", lambda name, prefix, interpreter: _UnavailableBackend()
    )

    inventory = service.list()

    assert inventory.rows == ()
    assert inventory.error == ""


def test_auto_uses_the_first_manager_that_works(monkeypatch, service: InventoryService) -> None:
    """uv missing on the machine means pip, silently - that is what 'automatic' is."""

    def build(prefix, *, name="", prefer=(), require=True):
        if name == "uv":
            return None
        return PipBackend(prefix=prefix, python=PYTHON)

    monkeypatch.setattr(inventory_module, "build_backend", build)
    monkeypatch.setattr(service, "_interpreter_has_pip", lambda: True)

    assert service.required_backend_missing() == ""


def test_auto_offers_to_set_pip_up_when_nothing_works(
    monkeypatch, service: InventoryService
) -> None:
    """No usable manager at all is a 'set pip up' offer, not a refusal."""

    def build(prefix, *, name="", prefer=(), require=True):
        return None

    monkeypatch.setattr(inventory_module, "build_backend", build)

    assert service.required_backend_missing() == "pip"


def test_a_forced_manager_is_reported_missing_rather_than_substituted(
    monkeypatch,
) -> None:
    """Settings say uv; uv is not installed. The answer names uv - pip is
    never silently substituted for the manager the user chose."""

    def build(prefix, *, name="", prefer=(), require=True):
        return None

    monkeypatch.setattr(inventory_module, "build_backend", build)
    forced = InventoryService(prefix=PREFIX, backend_name="uv")

    assert forced.required_backend_missing() == "uv"


# -- the commands that install the missing tool --------------------------------


def test_pip_is_bootstrapped_through_ensurepip(service: InventoryService) -> None:
    """pip ships inside the interpreter's own standard library."""

    plans = service.bootstrap_plans("pip", action=_upgrade())

    assert len(plans) == 1
    assert plans[0].argv == (str(PYTHON), "-m", "ensurepip", "--upgrade")
    assert plans[0].label == "install pip"


def test_uv_is_installed_with_pip_and_pip_is_ensured_first(
    monkeypatch, service: InventoryService
) -> None:
    """uv comes from the index through pip; pip itself may have to be ensured."""
    monkeypatch.setattr(service, "_interpreter_has_pip", lambda: False)

    plans = service.bootstrap_plans("uv", action=_upgrade())

    assert [plan.label for plan in plans] == ["install pip", "install uv"]
    assert plans[0].argv == (str(PYTHON), "-m", "ensurepip", "--upgrade")
    assert plans[1].argv == (str(PYTHON), "-m", "pip", "install", "--upgrade", "--", "uv")


def test_uv_needs_no_ensurepip_when_pip_is_already_there(
    monkeypatch, service: InventoryService
) -> None:
    monkeypatch.setattr(service, "_interpreter_has_pip", lambda: True)

    plans = service.bootstrap_plans("uv", action=_upgrade())

    assert [plan.label for plan in plans] == ["install uv"]


# -- planning the action through the not-yet-installed manager -----------------


def test_the_action_is_planned_against_the_path_the_bootstrap_fills(
    service: InventoryService,
) -> None:
    """The plan's first word is where uv will be once it is installed."""

    plan = service.plan_assuming_backend("uv", _upgrade())

    assert plan.backend == "uv"
    assert plan.label == "uv upgrade"
    assert plan.argv[0] == str(uv_path_in_prefix(service.environment))
    assert "--python" in plan.argv
    assert plan.argv[-1] == "requests"


def test_a_pip_plan_is_planned_the_way_pip_really_runs_it(
    service: InventoryService,
) -> None:
    plan = service.plan_assuming_backend("pip", _upgrade())

    assert plan.argv[:3] == (str(PYTHON), "-m", "pip")
    assert plan.backend == "pip"


def test_a_user_install_through_uv_is_refused_at_planning_time(
    service: InventoryService,
) -> None:
    """uv has no --user; the bootstrap flow must not plan a command it refuses."""
    action = PackageAction(
        kind=ActionKind.UPGRADE, environment=PREFIX, names=("requests",), user=True
    )

    with pytest.raises(BackendError):
        service.plan_assuming_backend("uv", action)


# -- the confirm-path facts the review hardened --------------------------------


def test_the_pip_probe_is_asked_once_and_cached_until_invalidated(
    monkeypatch, service: InventoryService
) -> None:
    """The probe spawns the interpreter; the confirm path runs on the GUI thread.

    A fresh spawn per click would freeze the window on every action, so the
    answer is memoized - and cleared exactly when a run could have changed
    it (the bootstrap that installs pip), never speculatively.
    """
    calls: list[list[str]] = []

    def fake_run(argv, *, timeout=None, **_kwargs):
        from pypackhub.core.runner import CommandResult

        calls.append(list(argv))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration=0.0)

    def build(prefix, *, name="", prefer=(), require=True):
        return PipBackend(prefix=prefix, python=PYTHON) if name == "pip" else None

    monkeypatch.setattr(inventory_module, "run", fake_run)
    monkeypatch.setattr(inventory_module, "build_backend", build)

    service.required_backend_missing()
    service.required_backend_missing()
    assert len(calls) == 1

    service.invalidate_backend_state()
    service.required_backend_missing()
    assert len(calls) == 2


def test_the_reachability_question_is_answered_for_the_named_backend(
    monkeypatch, service: InventoryService
) -> None:
    """The bootstrap asks the reachability question with the missing manager's
    own semantics, not with whatever backend the service would fall back to.

    uv has no user-site path, so a target whose imported copy lives in the
    user site is unreachable through it; pip, with its ``--user`` fallback,
    always reaches it. The same question must give the two different answers
    even while uv is not installed at all.
    """

    class _Meta:
        location = Path("/user/site/requests")

    class _Index:
        def get(self, name):
            return _Meta() if name == "requests" else None

    monkeypatch.setattr(service, "metadata_index", lambda: _Index())
    monkeypatch.setattr(
        inventory_module,
        "own_site_packages_directories",
        lambda prefix: (Path("/system/site"),),
    )

    action = _upgrade()
    assert service.backend_can_reach_targets(action, backend_name="pip")
    assert not service.backend_can_reach_targets(action, backend_name="uv")


def test_bootstrap_needs_elevation_only_where_the_site_is_locked(
    monkeypatch, service: InventoryService
) -> None:
    monkeypatch.setattr(inventory_module, "site_packages_writable", lambda prefix: False)
    assert service.bootstrap_needs_elevation()

    monkeypatch.setattr(inventory_module, "site_packages_writable", lambda prefix: True)
    assert not service.bootstrap_needs_elevation()


def test_an_environment_without_an_interpreter_refuses_rather_than_spawning(
    monkeypatch, service: InventoryService
) -> None:
    """A dead environment must not put the string "None" on a command line.

    There is nothing to bootstrap into without an interpreter: the pip probe
    answers no without spawning anything, and the assumed-backend plan
    refuses with a backend error the view reports - instead of showing
    "None -m pip install" in a confirmation dialog.
    """
    service._facts = InterpreterFacts(
        prefix=PREFIX,
        executable=None,
        kind=EnvironmentKind.VENV,
        externally_managed=False,
        externally_managed_marker=None,
        is_alias_stub=False,
    )

    def exploding_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("no probe may spawn without an interpreter")

    monkeypatch.setattr(inventory_module, "run", exploding_run)

    assert not service._interpreter_has_pip()

    with pytest.raises(BackendError):
        service.plan_assuming_backend("uv", _upgrade())
    with pytest.raises(BackendError):
        service.plan_assuming_backend("pip", _upgrade())


def test_configured_index_flags_ride_on_every_installing_command(
    monkeypatch,
) -> None:
    """The settings page's index URL is what installs resolve against.

    The flags are inserted before the ``--`` separator, where pip and uv read
    them, and uninstall commands stay untouched: an index has nothing to do
    with removing a local package.
    """
    monkeypatch.setattr(
        inventory_module,
        "build_backend",
        lambda prefix, *, name="", prefer=(), require=True: PipBackend(
            prefix=prefix, python=PYTHON
        ),
    )
    configured = InventoryService(
        prefix=PREFIX,
        prefer_uv=False,
        index_args=("--index-url", "https://mirror.example/simple"),
    )
    configured._facts = service_facts()

    upgrade = configured.plan(_upgrade())
    assert "--index-url" in upgrade.argv
    assert upgrade.argv.index("--index-url") < upgrade.argv.index("--")
    assert "https://mirror.example/simple" in upgrade.argv
    assert upgrade.display_command().count("--index-url") == 1

    uninstall = PackageAction(kind=ActionKind.UNINSTALL, environment=PREFIX, names=("requests",))
    removal = configured.plan(uninstall)
    assert "--index-url" not in removal.argv
