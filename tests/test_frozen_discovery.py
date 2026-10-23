"""What the bundled (PyInstaller) build must not get wrong.

A frozen build is its own runtime: ``sys.executable`` is the application
and ``sys.prefix`` is the bundle's internal folder. The report from the
first shipped build: the system's Pythons were not detected, none of the
machine's environments were either, and the application's own install
folder stood as the default environment.

Three behaviours keep that from coming back, and each is pinned here:

* the bundle's runtime is never offered as an environment;
* the machine's standard installs are found *directly*, because their
  parents (all of ``Program Files``, all of ``/usr``) are too big to scan
  and a conda base lives at the top of its install;
* anything that would have used ``sys.executable`` as a Python - the
  default prefix, the default venv interpreter - discovers a real one
  instead, and refuses clearly when the machine has none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import pypackhub.core.environments as environments_module
import pypackhub.core.platform_guard as platform_guard
from pypackhub.core.environments import (
    CreateEnvironmentError,
    create_environment,
    default_search_roots,
    detected_environments,
)
from pypackhub.core.runner import CommandResult

_IS_WINDOWS = sys.platform == "win32"


def _interpreter_of(prefix: Path) -> Path:
    """The executable path a real install carries on this platform."""
    return (
        prefix / ("Scripts" if _IS_WINDOWS else "bin") / ("python.exe" if _IS_WINDOWS else "python")
    )


def _fake_install(parent: Path, name: str = "Python313") -> Path:
    """A prefix that would pass for a real Python install on this platform."""
    prefix = parent / name
    scripts = prefix / ("Scripts" if _IS_WINDOWS else "bin")
    scripts.mkdir(parents=True)
    _interpreter_of(prefix).touch()
    return prefix.resolve()


def _fake_conda_env(parent: Path, name: str = "work") -> Path:
    """A conda environment: a ``conda-meta`` directory plus an interpreter."""
    prefix = parent / name
    (prefix / "conda-meta").mkdir(parents=True)
    scripts = prefix / ("Scripts" if _IS_WINDOWS else "bin")
    scripts.mkdir()
    _interpreter_of(prefix).touch()
    return prefix.resolve()


def test_a_frozen_build_never_offers_its_own_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported defect: the bundle's runtime folder was the default.

    ``sys.prefix`` in a frozen build is the application's own internal
    directory - a runtime, not an interpreter - so listing it as the
    running environment offers the user a target nothing can run in, and
    it lands first because it is the default.
    """
    bundle = tmp_path / "PyPackHub" / "_internal"
    bundle.mkdir(parents=True)
    real = _fake_install(tmp_path / "installs")

    monkeypatch.setattr(sys, "prefix", str(bundle))
    monkeypatch.setattr(environments_module, "is_frozen_build", lambda: True)
    monkeypatch.setattr(environments_module, "standard_python_prefixes", lambda: (real,))

    listing = detected_environments(roots=(), registered=())

    prefixes = [entry.prefix for entry in listing.environments]
    assert bundle not in prefixes, "the bundle's own runtime is not an environment"
    assert real in prefixes, "the machine's real Python is found instead"


def test_a_source_checkout_still_lists_its_running_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The freeze is the only reason to hide the running interpreter: under a
    real Python it stays in the listing, whatever the scanned roots hold."""
    monkeypatch.setattr(environments_module, "is_frozen_build", lambda: False)

    listing = detected_environments(roots=(), registered=())

    prefixes = [entry.prefix for entry in listing.environments]
    assert Path(sys.prefix).resolve() in prefixes


def test_the_standard_installs_are_offered_without_scanning_their_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build has to find ``Program Files``-style installs, whose
    parents must not be walked: the whole of Program Files is not a scan,
    and the install is offered directly instead."""
    real = _fake_install(tmp_path / "Program Files")

    monkeypatch.setattr(environments_module, "standard_python_prefixes", lambda: (real,))

    listing = detected_environments(roots=(), registered=())

    prefixes = [entry.prefix for entry in listing.environments]
    assert real in prefixes


def test_conda_bases_bring_their_envs_directory_into_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conda base is itself an environment *and* the parent of its named
    ones: the ``envs`` directory beside it joins the scan roots, so a
    ``conda create -n work`` environment is listed without registration."""
    base = _fake_conda_env(tmp_path, "miniconda3")
    named = _fake_conda_env(base / "envs")

    monkeypatch.setattr(environments_module, "standard_python_prefixes", lambda: (base,))

    assert (base / "envs") in default_search_roots()

    listing = detected_environments(roots=None, registered=())
    prefixes = [entry.prefix for entry in listing.environments]
    assert base in prefixes, "the conda base is itself an environment"
    assert named in prefixes, "its named environments are scanned beside it"


def test_current_prefix_in_a_frozen_build_is_a_real_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default environment's source: a frozen build cannot call its own
    prefix "the running interpreter", so it answers with the best real
    Python the machine has instead."""
    real = _fake_install(tmp_path)

    monkeypatch.setattr(platform_guard, "is_frozen_build", lambda: True)
    monkeypatch.setattr(platform_guard, "standard_python_prefixes", lambda: (real,))

    assert platform_guard.current_prefix() == real


def test_current_prefix_in_a_frozen_build_without_any_python_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine with no Python at all still needs *some* path; the bundle
    folder stands in only there, because nothing can be listed for it."""
    monkeypatch.setattr(platform_guard, "is_frozen_build", lambda: True)
    monkeypatch.setattr(platform_guard, "standard_python_prefixes", lambda: ())

    assert platform_guard.current_prefix() == Path(sys.prefix).resolve()


def test_create_environment_in_a_frozen_build_uses_a_discovered_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The venv dialog's default interpreter: ``PyPackHub.exe -m venv``
    would create nothing, so the bundle discovers a real Python to ask."""
    real = _fake_install(tmp_path)
    location = tmp_path / "new-env"
    location.mkdir()

    recorded: dict[str, tuple[str, ...]] = {}

    def fake_run(argv, **_kwargs: object) -> CommandResult:
        recorded["argv"] = tuple(argv)
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration=0.0)

    monkeypatch.setattr(environments_module, "is_frozen_build", lambda: True)
    monkeypatch.setattr(environments_module, "standard_interpreter_prefix", lambda: real)
    monkeypatch.setattr(environments_module, "run", fake_run)

    result = create_environment(location)

    assert result.success, result.message
    assert recorded["argv"][:2] == (recorded["argv"][0], "-m"), "the venv module runs"
    assert recorded["argv"][0] != sys.executable, "not the bundle's own executable"
    assert recorded["argv"][0] == str(_interpreter_of(real).resolve())


def test_create_environment_in_a_frozen_build_refuses_without_any_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine with no Python gets a refusal that says what to do, not a
    venv command run through something that is not an interpreter."""
    monkeypatch.setattr(environments_module, "is_frozen_build", lambda: True)
    monkeypatch.setattr(environments_module, "standard_interpreter_prefix", lambda: None)

    with pytest.raises(CreateEnvironmentError) as info:
        create_environment(tmp_path / "env")

    assert "no Python interpreter" in str(info.value)
