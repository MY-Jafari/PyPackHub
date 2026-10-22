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
    UnsupportedBackendError,
    script_path_in_prefix,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.pipx import PipxBackend
from pypackhub.core.backends.poetry import PoetryBackend
from pypackhub.core.backends.uv import UvBackend, uv_path_in_prefix
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

    def supports_environment(self, path: Path) -> bool:
        return True

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


def test_the_reachability_question_is_vacuous_for_pipx(
    monkeypatch, service: InventoryService
) -> None:
    """A pipx upgrade is never sent to the pip fallback.

    The reachability check exists for the user-site shadowing case: a backend
    that writes only the environment's own site-packages cannot change what
    the interpreter imports when a user-site copy shadows it, so the honest
    answer for uv is "no" and the view offers pip instead. pipx writes
    neither site - its applications live in its own home - so the same
    evidence must not turn a pipx the user chose into a pip they did not:
    the question is about the selected environment, and pipx never touches
    it. The same upgrade answers "yes" through pipx and "no" through uv.
    """

    class _Metadata:
        location = Path("/user-site/requests")

    class _Index:
        def get(self, name: str) -> _Metadata:
            return _Metadata()

    monkeypatch.setattr(service, "metadata_index", lambda: _Index())
    monkeypatch.setattr(
        inventory_module, "own_site_packages_directories", lambda prefix: (Path("/own/site"),)
    )

    assert service.backend_can_reach_targets(_upgrade(), backend_name="uv") is False
    assert service.backend_can_reach_targets(_upgrade(), backend_name="pipx") is True


def test_the_elevation_answer_belongs_to_the_backend(monkeypatch) -> None:
    """Same environment, different answers, because each backend knows what
    its own commands write.

    The report behind the rule: a pipx forced on a locked all-users
    environment had its upgrade elevated with the installation that preceded
    it, which threw away the streamed output exactly when the user needed to
    read why the command failed. pipx writes its own home; pip has the user
    site to fall back to; the project tools write the project folder; only
    uv is left looking at the locked site-packages.
    """
    monkeypatch.setattr("pypackhub.core.metadata.site_packages_writable", lambda prefix: False)
    monkeypatch.setattr("pypackhub.core.metadata.directory_writable", lambda directory: True)

    action = _upgrade()
    assert PipBackend(prefix=PREFIX, python=PYTHON).needs_elevation(action, PREFIX) is False
    assert UvBackend(executable=Path("/uv"), python=PYTHON).needs_elevation(action, PREFIX) is True
    assert PipxBackend(executable=Path("/pipx")).needs_elevation(action, PREFIX) is False
    assert PoetryBackend(executable=Path("/poetry")).needs_elevation(action, Path("/proj")) is False

    # A locked project folder is the project tools' elevation case: their
    # commands write it before anything else.
    monkeypatch.setattr("pypackhub.core.metadata.directory_writable", lambda directory: False)
    assert PoetryBackend(executable=Path("/poetry")).needs_elevation(action, Path("/proj")) is True


def test_a_bootstrapped_pipx_never_elevates_its_own_action(
    monkeypatch, service: InventoryService
) -> None:
    """The bootstrap batch's last step is judged by the backend it will run
    through, not by the blanket answer its install steps needed: on the same
    locked environment, uv's upgrade keeps the OS's help and pipx's does
    not."""
    monkeypatch.setattr("pypackhub.core.metadata.site_packages_writable", lambda prefix: False)

    assert service.elevation_assuming_backend("uv", _upgrade()) is True
    assert service.elevation_assuming_backend("pipx", _upgrade()) is False


def test_the_policy_answer_survives_a_machine_with_no_manager(monkeypatch, tmp_path: Path) -> None:
    """The bootstrap offer is made in exactly this state: nothing resolves.

    The report: with a forced pipx that was not installed, the confirm path
    asked for the policy *before* asking what was missing - and the policy
    resolved a backend, which raised, which killed the offer. The platform
    guard still answers for the prefix, and the offer to install a manager
    must survive the trip to being made.
    """

    def build(prefix, *, name="", prefer=(), require=True):
        raise UnsupportedBackendError(
            "no usable package manager was found (tried: " + (name or "nothing") + ")"
        )

    monkeypatch.setattr(inventory_module, "build_backend", build)
    service = InventoryService(prefix=tmp_path, backend_name="pipx")

    policy = service.policy()

    assert policy.reason_code == "target.base_interpreter"
    assert service.required_backend_missing() == "pipx"


def test_the_availability_probe_answers_by_resolution_only(
    monkeypatch,
) -> None:
    """The settings page asks on the GUI thread, so the answer is resolution
    only: every finder is a PATH walk, and a machine with nothing on it
    answers "not installed" for every manager without spawning anything."""
    from pypackhub.core.runner import ExecutableNotFoundError

    def refuse(name: str) -> Path:
        raise ExecutableNotFoundError(name)

    monkeypatch.setattr("pypackhub.core.runner.resolve_executable", refuse)

    for name in inventory_module.KNOWN_BACKENDS:
        assert inventory_module.manager_available(name) is False, name
    assert inventory_module.manager_available("rye") is False

    monkeypatch.setattr(
        "pypackhub.core.runner.resolve_executable", lambda name: Path("/tools") / name
    )
    assert inventory_module.manager_available("pipx") is True


def test_an_install_only_batch_is_recorded_as_the_manager_it_installs(
    service: InventoryService,
) -> None:
    """The settings page's install is an action like any other; its history
    row names the manager it installed."""
    action = service.action_for_manager_install("pipx")

    assert action.kind is ActionKind.INSTALL
    assert action.specs == ("pipx",)


def test_only_what_pip_installed_is_offered_for_removal() -> None:
    """pip takes back out what pip put in - and nothing else.

    pip itself is not offered: it is the baseline every bootstrap path
    starts from. conda and mamba are not pip packages, so whatever put them
    on the machine was not pip, and what removes them must not be either.
    """
    for name in ("uv", "poetry", "pipx", "pipenv", "pdm"):
        assert inventory_module.manager_removable(name) is True, name

    for name in ("pip", "conda", "mamba", "rye", "auto"):
        assert inventory_module.manager_removable(name) is False, name


def test_only_what_the_bootstrap_can_reach_is_offered_for_install() -> None:
    """The settings page asks "install it now?" only where the answer can
    be acted on.

    conda and mamba are the reported dead-end: the question was asked, the
    user said yes, and the only honest answer - "it is not a pip package" -
    arrived as a toast somewhere else. A manager this application cannot
    install is never offered, and unknown names are not either: a page only
    asks questions it can act on.
    """
    for name in ("uv", "pip", "poetry", "pipx", "pipenv", "pdm"):
        assert inventory_module.manager_installable(name) is True, name

    for name in ("conda", "mamba", "rye", "auto", ""):
        assert inventory_module.manager_installable(name) is False, name


def test_a_manager_out_of_its_scope_is_not_missing_but_wrong_for_here(
    service: InventoryService, tmp_path: Path
) -> None:
    """The report: poetry forced in the settings was reported "missing" on
    a plain interpreter - because it refuses to manage one - so upgrades
    offered to install an already-installed poetry, or said only "run with
    pip". Whether a manager is the right *kind* for an environment is
    answered from the environment itself, installed or not, so the refusal
    can say the true thing.
    """
    # The service's prefix is a plain environment: no project markers, no
    # conda metadata. The interpreter-needing managers (pip, uv) are judged
    # below on a folder that has one, the way a real environment does.
    assert service.manager_applies_to_environment("poetry") is False
    assert service.manager_applies_to_environment("pipenv") is False
    assert service.manager_applies_to_environment("pdm") is False
    assert service.manager_applies_to_environment("conda") is False

    # uv names an interpreter with --python, and so does pip: a folder with
    # one is theirs to manage, a bare fake path without one is not.
    with_python = tmp_path / "env"
    with_python.mkdir()
    for name in ("python.exe", "Scripts/python.exe", "bin/python"):
        target = with_python / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    python_service = InventoryService(prefix=with_python)
    assert python_service.manager_applies_to_environment("pip") is True
    assert python_service.manager_applies_to_environment("uv") is True
    assert service.manager_applies_to_environment("pip") is False
    assert service.manager_applies_to_environment("uv") is False

    # A folder carrying poetry's own markers is poetry's to manage - and
    # not pipenv's, whose marker is a Pipfile.
    project = tmp_path / "poetry-project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'demo'\nversion = '0'\n", encoding="utf-8"
    )
    poetry_service = InventoryService(prefix=project)
    assert poetry_service.manager_applies_to_environment("poetry") is True
    assert poetry_service.manager_applies_to_environment("pipenv") is False

    # Unknown names answer True: the missing-manager path is the one that
    # explains them.
    assert service.manager_applies_to_environment("rye") is True


def test_the_user_site_question_is_answered_once_for_both_callers(
    monkeypatch, service: InventoryService
) -> None:
    """``action_targets_user_site`` states the reachability test's evidence:
    the pip fallback asks it directly, to decide whether its plan needs
    ``--user`` - and the reachability check asks the same question about
    the backend instead."""
    from pypackhub.core.backends.base import PackageAction  # noqa: F401 - clarity

    class _Metadata:
        location = Path("/user-site/requests")

    class _Index:
        def get(self, name: str) -> _Metadata:
            return _Metadata()

    monkeypatch.setattr(service, "metadata_index", lambda: _Index())
    monkeypatch.setattr(
        inventory_module, "own_site_packages_directories", lambda prefix: (Path("/own/site"),)
    )

    assert service.action_targets_user_site(_upgrade()) is True
    assert service.backend_can_reach_targets(_upgrade(), backend_name="uv") is False

    class _OwnMetadata:
        location = Path("/own/site/requests")

    class _OwnIndex:
        def get(self, name: str) -> _OwnMetadata:
            return _OwnMetadata()

    monkeypatch.setattr(service, "metadata_index", lambda: _OwnIndex())

    assert service.action_targets_user_site(_upgrade()) is False
    assert service.backend_can_reach_targets(_upgrade(), backend_name="uv") is True


def test_a_manager_removal_is_one_pip_command_with_the_exact_target(
    service: InventoryService,
) -> None:
    action = service.action_for_manager_removal("pipx")
    plan = service.manager_removal_plan("pipx", action)

    assert plan.argv == (str(PYTHON), "-m", "pip", "uninstall", "--yes", "--", "pipx")
    assert plan.backend == "pip"
    assert action.kind is ActionKind.UNINSTALL
    assert action.names == ("pipx",)


def test_managers_pip_cannot_remove_are_refused_with_the_reason(
    service: InventoryService,
) -> None:
    action = service.action_for_manager_removal("conda")

    with pytest.raises(BackendError) as raised:
        service.manager_removal_plan("conda", action)

    assert "not a manager this application removes" in str(raised.value)


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


# -- the five new managers: bootstrap, registry, policy -------------------------


@pytest.mark.parametrize("tool", ["poetry", "pipx", "pipenv", "pdm"])
def test_a_pip_installable_tool_is_bootstrapped_with_pip(
    monkeypatch, service: InventoryService, tool: str
) -> None:
    """poetry, pipx, pipenv and pdm are index packages: one pip install makes
    the forced choice usable, with pip ensured first when it is missing."""
    monkeypatch.setattr(service, "_interpreter_has_pip", lambda: False)

    plans = service.bootstrap_plans(tool, action=_upgrade())

    assert [plan.label for plan in plans] == ["install pip", f"install {tool}"]
    assert plans[1].argv == (str(PYTHON), "-m", "pip", "install", "--upgrade", "--", tool)


def test_conda_is_never_offered_as_a_pip_install(
    service: InventoryService,
) -> None:
    """conda and mamba are not PyPI packages; the bootstrap says where they
    come from instead of queueing an install that cannot work."""
    with pytest.raises(BackendError) as raised:
        service.bootstrap_plans("conda", action=_upgrade())

    assert "not a pip package" in str(raised.value)


def test_a_bootstrapped_project_tool_is_planned_against_its_future_script(
    tmp_path: Path,
) -> None:
    """The plan's first word is where pip is about to put the tool's script,
    and a poetry plan runs inside the project the action names."""
    service = InventoryService(prefix=tmp_path)
    service._facts = service_facts()
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    action = PackageAction(kind=ActionKind.INSTALL, environment=project, specs=("flask",))

    plan = service.plan_assuming_backend("poetry", action)

    assert plan.argv[0] == str(script_path_in_prefix(tmp_path, "poetry"))
    assert plan.cwd == project
    assert plan.backend == "poetry"


def test_a_pipx_downgrade_is_refused_even_before_pipx_exists(
    tmp_path: Path,
) -> None:
    """The capability is checked before the plan exists, so an action the
    tool cannot honour is refused with no command to show."""
    service = InventoryService(prefix=tmp_path)
    service._facts = service_facts()
    action = PackageAction(
        kind=ActionKind.DOWNGRADE, environment=tmp_path, names=("black",), version="1.0.0"
    )

    with pytest.raises(BackendError):
        service.plan_assuming_backend("pipx", action)


def test_the_preference_order_follows_the_location(tmp_path: Path) -> None:
    """ "automatic" is the environment's own answer, not a fixed list."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert inventory_module._preference_order(plain, prefer_uv=True) == ("uv", "pip")
    assert inventory_module._preference_order(plain, prefer_uv=False) == ("pip",)

    conda = tmp_path / "conda-env"
    (conda / "conda-meta").mkdir(parents=True)
    assert inventory_module._preference_order(conda, prefer_uv=True) == (
        "mamba",
        "conda",
        "uv",
        "pip",
    )

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    assert inventory_module._preference_order(project, prefer_uv=True) == ("poetry",)


def test_a_plain_library_folder_keeps_the_pip_family_order(tmp_path: Path) -> None:
    """A ``pyproject.toml`` without a project tool's markers is nobody's
    project: it is not a PROJECT location, so the plain-interpreter order
    stands. The registry's location guard is what keeps pip from actually
    running there - it has no interpreter - not the order."""
    project = tmp_path / "lib"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert inventory_module._preference_order(project, prefer_uv=True) == ("uv", "pip")


def test_the_service_asks_the_backend_for_the_policy(tmp_path: Path) -> None:
    """pipx's commands never touch the selected environment, so its policy
    answers for its own home even when the selected prefix is a base
    interpreter that would otherwise carry a warning."""
    service = InventoryService(prefix=tmp_path, backend=PipxBackend(executable=Path("/pipx")))

    assert service.policy().reason_code == "target.pipx_home"
    assert service.policy().allowed is True


def test_index_flags_ride_pip_plans_but_not_the_project_tools(monkeypatch, tmp_path: Path) -> None:
    """``--index-url`` is pip's vocabulary. Carrying it into a poetry plan
    would produce a confirmed command poetry refuses to parse."""
    monkeypatch.setattr(
        inventory_module,
        "build_backend",
        lambda prefix, *, name="", prefer=(), require=True: (
            PipBackend(prefix=prefix, python=PYTHON)
            if name == "pip"
            else PoetryBackend(executable=Path("/poetry"))
        ),
    )

    def service_forced(name: str) -> InventoryService:
        service = InventoryService(
            prefix=tmp_path,
            backend_name=name,
            index_args=("--index-url", "https://mirror.example/simple"),
        )
        service._facts = service_facts()
        return service

    pip_plan = service_forced("pip").plan(_upgrade())
    assert "--index-url" in pip_plan.argv

    poetry_plan = service_forced("poetry").plan(_upgrade())
    assert "--index-url" not in poetry_plan.argv
    assert poetry_plan.backend == "poetry"
