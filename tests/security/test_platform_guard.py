"""SPEC sections 11.5 and 13: protecting the interpreters people did not mean to touch.

The Phase 1 acceptance criteria call out "system-Python protection works", and
this is where that is proven: a base interpreter or a PEP 668 managed one is
refused, and the refusal carries the reason and the suggestion the UI needs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pypackhub.core.platform_guard import (
    EnvironmentKind,
    Severity,
    detect_kind,
    find_externally_managed_marker,
    inspect,
    is_windows_app_alias_stub,
    modification_policy,
)

pytestmark = pytest.mark.security


def make_venv(path: Path) -> Path:
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (path / "bin" / "python").write_text("", encoding="utf-8")

    return path


def make_base_interpreter(path: Path) -> Path:
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "bin" / "python3").write_text("", encoding="utf-8")

    return path


def test_a_virtual_environment_is_isolated_and_allowed(tmp_path: Path) -> None:
    facts = inspect(make_venv(tmp_path / "env"))

    assert facts.kind is EnvironmentKind.VENV
    assert facts.is_isolated is True

    policy = modification_policy(facts)
    assert policy.allowed is True
    assert policy.requires_confirmation is True
    assert policy.reason_code == "target.isolated_environment"


def test_a_conda_environment_is_isolated_and_allowed(tmp_path: Path) -> None:
    prefix = tmp_path / "conda-env"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "python.exe").write_text("", encoding="utf-8")

    facts = inspect(prefix)

    assert facts.kind is EnvironmentKind.CONDA
    assert modification_policy(facts).allowed is True


def test_a_pyenv_environment_is_allowed_with_a_note(tmp_path: Path) -> None:
    prefix = tmp_path / ".pyenv" / "versions" / "project"
    make_venv(prefix)

    facts = inspect(prefix)

    assert facts.kind is EnvironmentKind.PYBENV

    policy = modification_policy(facts)
    assert policy.allowed is True
    assert policy.severity is Severity.WARNING
    assert policy.reason_code == "target.pyenv"


def test_a_base_interpreter_needs_an_explicit_override(tmp_path: Path) -> None:
    facts = inspect(make_base_interpreter(tmp_path / "python313"))

    assert facts.kind is EnvironmentKind.SYSTEM
    assert facts.is_isolated is False

    policy = modification_policy(facts)
    assert policy.allowed is False
    assert policy.requires_confirmation is True
    assert policy.severity is Severity.WARNING
    assert policy.reason_code == "target.base_interpreter"
    assert policy.suggestion

    overridden = modification_policy(facts, override=True)
    assert overridden.allowed is True
    # Even with the override, this is never a silent action.
    assert overridden.requires_confirmation is True


def test_pep668_marker_blocks_modification(tmp_path: Path) -> None:
    prefix = tmp_path / "distro-python"
    stdlib = prefix / "lib" / "python3.13"
    stdlib.mkdir(parents=True)
    marker = stdlib / "EXTERNALLY-MANAGED"
    marker.write_text("[externally-managed]\nError = use a venv\n", encoding="utf-8")

    facts = inspect(prefix)

    assert facts.externally_managed is True
    assert facts.externally_managed_marker == marker
    assert find_externally_managed_marker(prefix) == marker

    policy = modification_policy(facts)
    assert policy.allowed is False
    assert policy.severity is Severity.DANGER
    assert policy.would_need_installer_override is True
    assert "--break-system-packages" in policy.explanation

    # An override is possible, but it is the caller's job to have asked first.
    assert modification_policy(facts, override=True).allowed is True


def test_no_marker_means_not_externally_managed(tmp_path: Path) -> None:
    assert inspect(make_venv(tmp_path / "env")).externally_managed is False


def test_windows_app_alias_stub_is_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub_directory = tmp_path / "Microsoft" / "WindowsApps"
    stub_directory.mkdir(parents=True)
    stub = stub_directory / "python.exe"
    stub.write_bytes(b"")

    # `os.name` is read at call time, so this covers the Windows branch on any
    # runner. The context manager restores it immediately afterwards.
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        assert is_windows_app_alias_stub(stub) is True


def test_a_real_executable_is_not_an_alias_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = tmp_path / "WindowsApps" / "python.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ" + b"\x00" * 64)

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        assert is_windows_app_alias_stub(real) is False


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "the stub is simulated by patching os.name, and Python 3.13's pathlib "
        "dispatches on os.name at call time - on a POSIX host the patch turns "
        "Path() into WindowsPath against a POSIX filesystem"
    ),
)
def test_alias_stub_target_is_never_modifiable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub_directory = tmp_path / "WindowsApps"
    stub_directory.mkdir()
    stub = stub_directory / "python.exe"
    stub.write_bytes(b"")

    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        facts = inspect(tmp_path, executable=stub)

    assert facts.is_alias_stub is True

    policy = modification_policy(facts)
    assert policy.allowed is False
    assert policy.reason_code == "target.alias_stub"


def test_detect_kind_of_a_plain_directory_is_system(tmp_path: Path) -> None:
    assert detect_kind(tmp_path) is EnvironmentKind.SYSTEM
