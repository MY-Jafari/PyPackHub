"""Administrator privileges: for the one command that has nowhere else to write.

uv installs into the environment's own site-packages and nowhere else - pip
falls back to the user site when the all-users directory is not writable, uv
has no such fallback - so on an all-users install a uv action needs the OS's
help. SPEC section 11.5 shapes the feature: elevate one action, explain it
beforehand, use the OS's standard mechanism, persist nothing.

What is tested here is everything short of the OS's own consent dialog, which
no test suite may click: the writability fact the decision rests on, the
per-backend decision, the service's routing, and the runner's argument
handling and refusal paths.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pypackhub.core import metadata, runner
from pypackhub.core.backends.base import (
    ActionKind,
    CommandPlan,
    PackageAction,
    UnsupportedBackendError,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.uv import UvBackend
from pypackhub.core.inventory import InventoryService
from pypackhub.core.runner import (
    CommandResult,
    ExecutableNotFoundError,
    elevation_supported,
    run_elevated,
)


def _upgrade_action(environment: Path) -> PackageAction:
    return PackageAction(kind=ActionKind.UPGRADE, environment=environment, names=("pip",))


# -- the writability fact ------------------------------------------------------


def test_a_venv_style_prefix_is_writable(tmp_path: Path) -> None:
    site = tmp_path / "Lib" / "site-packages"
    site.mkdir(parents=True)

    assert metadata.site_packages_writable(tmp_path)
    # The probe cleans up after itself; a leftover would be a file the app
    # wrote into the very environment it only meant to look at.
    assert list(site.iterdir()) == []


def test_a_prefix_with_no_site_packages_is_not_a_refusal(tmp_path: Path) -> None:
    """Nothing was measured, so nothing is claimed: the manager reports its own error."""
    assert metadata.site_packages_writable(tmp_path)


def test_a_site_packages_the_user_cannot_write_needs_elevation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        metadata, "own_site_packages_directories", lambda prefix: (tmp_path / "locked",)
    )

    assert not metadata.site_packages_writable(tmp_path)


def test_a_regular_file_is_not_a_writable_site_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe fails rather than guesses; a file is not a directory to write into."""
    victim = tmp_path / "not-a-directory.txt"
    victim.write_text("nope", encoding="utf-8")

    monkeypatch.setattr(metadata, "own_site_packages_directories", lambda prefix: (victim,))

    assert not metadata.site_packages_writable(tmp_path)


# -- which backends need the help ----------------------------------------------


def _base_interpreter(prefix: Path) -> Path:
    """A prefix that looks like a real CPython installation, not a bare tree."""
    (prefix / "lib" / "python3.13" / "site-packages").mkdir(parents=True, exist_ok=True)
    (prefix / "python.exe").write_text("", encoding="utf-8")
    return prefix


def _install_metadata(site: Path, name: str, version: str) -> None:
    """Write a distribution the scanner can find, next to its module."""
    directory = site / f"{name}-{version}.dist-info"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )


@pytest.fixture
def shadowed_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A base interpreter whose user site shadows one of its own packages.

    This is the layout behind the report: an all-users Python where
    ``uv pip install --upgrade`` - elevated or not - wrote a new ``shadowed``
    into the system site-packages, while the interpreter still imports the
    user-site copy. The index has to resolve the *user-site* one.
    """
    prefix = _base_interpreter(tmp_path / "python313")
    own_site = prefix / "lib" / "python3.13" / "site-packages"

    appdata = tmp_path / "AppData"
    user_site = appdata / "Python" / "Python313" / "site-packages"
    monkeypatch.setenv("APPDATA", str(appdata))

    # The copy Python actually imports - the old one, in the user site.
    _install_metadata(user_site, "shadowed", "1.0")
    # The copy the elevated uv upgrade installed - newer, and ignored.
    _install_metadata(own_site, "shadowed", "2.0")
    # A package that lives only where uv writes, so nothing shadows it.
    _install_metadata(own_site, "plain", "1.0")

    return prefix


def test_uv_cannot_change_a_package_the_user_site_shadows(
    shadowed_environment: Path,
) -> None:
    """The exact scenario from the report: the run 'succeeds', nothing changes.

    uv wrote a newer copy into the environment's own site-packages and exited
    0 - and the table kept showing the old version, correctly, because
    ``import`` never looks there. The action is refused before it runs.
    """
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )

    assert not service.backend_can_reach_targets(
        PackageAction(
            kind=ActionKind.UPGRADE, environment=shadowed_environment, names=("shadowed",)
        )
    )


def test_uv_can_change_a_package_that_lives_where_it_writes(
    shadowed_environment: Path,
) -> None:
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )

    assert service.backend_can_reach_targets(
        PackageAction(kind=ActionKind.UPGRADE, environment=shadowed_environment, names=("plain",))
    )


def test_pip_can_always_reach_the_shadowing_copy(shadowed_environment: Path) -> None:
    """pip upgrades the copy in the user site, as a normal user, no UAC."""
    service = InventoryService(
        prefix=shadowed_environment, backend=PipBackend(prefix=shadowed_environment)
    )

    assert service.backend_can_reach_targets(
        PackageAction(
            kind=ActionKind.UPGRADE, environment=shadowed_environment, names=("shadowed",)
        )
    )


def test_a_package_that_is_not_installed_yet_is_reachable(
    shadowed_environment: Path,
) -> None:
    """Nothing shadows a fresh install; it lands where import will look."""
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )

    assert service.backend_can_reach_targets(
        PackageAction(kind=ActionKind.INSTALL, environment=shadowed_environment, specs=("absent",))
    )


def test_install_specs_reduce_to_bare_names_for_the_check(
    shadowed_environment: Path,
) -> None:
    """``selenium>=4`` is about selenium; the specifier part is not a name."""
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )

    assert not service.backend_can_reach_targets(
        PackageAction(
            kind=ActionKind.INSTALL, environment=shadowed_environment, specs=("shadowed>=1",)
        )
    )


def test_uv_needs_elevation_only_when_the_site_packages_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = InventoryService(prefix=tmp_path, backend=UvBackend(python=Path("python")))
    action = _upgrade_action(tmp_path)

    monkeypatch.setattr("pypackhub.core.inventory.site_packages_writable", lambda prefix: False)
    assert service.backend_needs_elevation(action)

    monkeypatch.setattr("pypackhub.core.inventory.site_packages_writable", lambda prefix: True)
    assert not service.backend_needs_elevation(action)


# -- the pip fallback for what uv cannot reach ----------------------------------


def test_the_fallback_plan_is_pips_own_command(shadowed_environment: Path) -> None:
    """Same action, other backend: the plan carries pip's argv, not uv's."""
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )
    action = PackageAction(
        kind=ActionKind.UPGRADE,
        environment=shadowed_environment,
        names=("shadowed",),
        user=True,
    )

    plan = service.plan_with_backend("pip", action)

    assert plan.backend == "pip"
    assert "install" in plan.argv
    assert "--user" in plan.argv
    assert plan.argv[-1] == "shadowed"


def test_asking_for_pip_means_pip(
    shadowed_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prefer=()`` keeps the request literal: no silent substitution.

    A fallback that quietly picked "whatever is installed" could hand the
    action right back to the backend that was just refused.
    """

    def broken(prefix, *, name="", prefer=(), require=True):
        raise UnsupportedBackendError(f"{name} is not available")

    monkeypatch.setattr("pypackhub.core.inventory.build_backend", broken)
    service = InventoryService(
        prefix=shadowed_environment, backend=UvBackend(python=Path("python"))
    )
    action = PackageAction(
        kind=ActionKind.UPGRADE, environment=shadowed_environment, names=("shadowed",)
    )

    with pytest.raises(UnsupportedBackendError):
        service.plan_with_backend("pip", action)


def test_pip_never_needs_elevation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pip has a user-site path of its own: there is always somewhere to go."""
    service = InventoryService(
        prefix=tmp_path, backend=PipBackend(prefix=tmp_path, python=Path("python"))
    )
    monkeypatch.setattr("pypackhub.core.inventory.site_packages_writable", lambda prefix: False)

    assert not service.backend_needs_elevation(_upgrade_action(tmp_path))


def test_an_elevated_run_goes_through_the_backends_elevated_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same plan, different privileges: the elevated flag must not change the command."""
    recorded: dict[str, tuple[str, ...]] = {}
    backend = UvBackend(python=Path("python"))

    def fake_execute_elevated(plan: CommandPlan, *, cancel=None, timeout=None) -> CommandResult:
        recorded["argv"] = plan.argv
        return CommandResult(argv=plan.argv, returncode=0, stdout="", stderr="", duration=0.0)

    monkeypatch.setattr(backend, "execute_elevated", fake_execute_elevated)
    service = InventoryService(prefix=tmp_path, backend=backend)
    plan = CommandPlan(
        argv=("uv", "pip", "install", "--upgrade", "--", "pip"),
        action=_upgrade_action(tmp_path),
        label="uv upgrade",
        backend="uv",
    )

    result = service.execute(plan, elevated=True)

    assert result.ok
    assert recorded["argv"] == plan.argv


# -- the runner's elevated path ------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="UAC is a Windows mechanism")
def test_windows_offers_elevation() -> None:
    assert elevation_supported()


def test_macos_refuses_rather_than_falling_back_to_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third operating system is a refusal, not a shell.

    macOS's own route would be embedding the command inside an AppleScript
    string - exactly the string-command execution the security rules forbid -
    so the runner declines, says what to do instead, and never starts a
    process. The platform is pinned rather than skipped so that every leg
    of the CI matrix covers this branch, macOS runners included.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    # ``elevation_supported`` dispatches on ``os.name`` first (UAC before the
    # darwin refusal), so a Windows host would never reach the branch under
    # test. Pinning it here is safe: unlike the platform-guard tests, this
    # window constructs no ``Path``, which is the one thing that dispatches
    # on ``os.name`` at call time.
    monkeypatch.setattr(os, "name", "posix")

    assert not elevation_supported()

    def must_not_run(argv: object, **_kwargs: object) -> CommandResult:
        raise AssertionError("the darwin path must not launch a process")

    monkeypatch.setattr(runner, "run", must_not_run)

    result = runner._run_elevated_posix(
        ("some-manager", "pip", "install", "--", "pip"), timeout=None, cancel=None
    )

    assert not result.ok
    assert "use pip" in result.stderr


def test_run_elevated_resolves_the_executable_like_any_other_command(
    tmp_path: Path,
) -> None:
    """Elevation is not a way around the resolution rules: a missing program is refused."""
    with pytest.raises(ExecutableNotFoundError):
        run_elevated([str(tmp_path / "definitely-not-here.exe"), "--version"])


@pytest.mark.skipif(
    sys.platform == "darwin", reason="macOS has no pkexec; the runner refuses there"
)
def test_run_elevated_without_a_mechanism_is_an_error_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The posix path reports the missing mechanism instead of raising."""
    executable = tmp_path / "fake-manager"
    executable.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner.shutil, "which", lambda name: None)

    result = runner._run_elevated_posix(
        (str(executable), "pip", "install", "--", "pip"), timeout=None, cancel=None
    )

    assert not result.ok
    assert "pkexec" in result.stderr


@pytest.mark.skipif(
    sys.platform == "darwin", reason="macOS has no pkexec; the runner refuses there"
)
def test_pkexec_receives_the_command_as_an_argument_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """polkit takes an argument vector, so the hardened runner applies unchanged."""
    executable = tmp_path / "fake-manager"
    executable.write_text("", encoding="utf-8")

    captured: dict[str, tuple[str, ...]] = {}

    def fake_run(argv, **_kwargs: object) -> CommandResult:
        captured["argv"] = tuple(argv)
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration=0.0)

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/pkexec")

    result = runner._run_elevated_posix(
        (str(executable), "pip", "install", "--", "pip"), timeout=None, cancel=None
    )

    assert result.ok
    assert captured["argv"] == (
        "pkexec",
        str(executable),
        "pip",
        "install",
        "--",
        "pip",
    )
