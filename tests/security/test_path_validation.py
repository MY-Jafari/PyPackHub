"""SPEC section 11.9: deletion is provably safe, or it does not happen.

These tests are the reason `ensure_deletable_environment` exists as a separate,
testable function instead of a few `if`s next to the delete button.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypackhub.core.validation import (
    Reason,
    ValidationError,
    ensure_deletable_environment,
    ensure_inside,
    is_verified_environment,
)

pytestmark = pytest.mark.security


def make_virtual_environment(path: Path) -> Path:
    """A directory that looks enough like a venv to be recognised as one."""

    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (path / "bin" / "python").write_text("", encoding="utf-8")

    return path


def make_conda_environment(path: Path) -> Path:
    (path / "conda-meta").mkdir(parents=True, exist_ok=True)
    (path / "python.exe").write_text("", encoding="utf-8")

    return path


def test_an_unrelated_directory_is_not_an_environment(tmp_path: Path) -> None:
    target = tmp_path / "holiday-photos"
    target.mkdir()

    assert is_verified_environment(target) is False

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(target)

    assert info.value.reason is Reason.NOT_VERIFIED_ENVIRONMENT


def test_a_virtual_environment_is_recognised(tmp_path: Path) -> None:
    target = make_virtual_environment(tmp_path / "env")

    assert is_verified_environment(target) is True
    assert ensure_deletable_environment(target) == target.resolve()


def test_a_conda_environment_is_recognised(tmp_path: Path) -> None:
    target = make_conda_environment(tmp_path / "conda-env")

    assert is_verified_environment(target) is True
    assert ensure_deletable_environment(target) == target.resolve()


def test_the_home_directory_is_refused() -> None:
    home = Path.home()

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(home, home=home)

    assert info.value.reason is Reason.FORBIDDEN_TARGET


def test_a_parent_of_the_home_directory_is_refused() -> None:
    home = Path.home()

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(home.parent, home=home)

    assert info.value.reason is Reason.FORBIDDEN_TARGET


def test_a_filesystem_root_is_refused() -> None:
    root = Path(Path.home().anchor)

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(root)

    assert info.value.reason is Reason.FORBIDDEN_TARGET


def test_a_protected_python_installation_is_refused(tmp_path: Path) -> None:
    protected = tmp_path / "python-install"
    environment = make_virtual_environment(protected / "env")

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(environment, protected_prefixes=(protected,))

    assert info.value.reason is Reason.FORBIDDEN_TARGET


def test_the_running_interpreter_is_always_protected() -> None:
    import sys

    with pytest.raises(ValidationError):
        ensure_deletable_environment(Path(sys.base_prefix))


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(tmp_path / "does-not-exist")

    assert info.value.reason is Reason.NOT_FOUND


def test_a_file_is_not_a_directory(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("", encoding="utf-8")

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(target)

    assert info.value.reason is Reason.NOT_A_DIRECTORY


def test_a_path_inside_the_environment_is_accepted(tmp_path: Path) -> None:
    environment = make_virtual_environment(tmp_path / "env")
    target = environment / "bin" / "python"

    assert ensure_inside(target, environment) == target.resolve()


def test_a_path_outside_the_environment_is_refused(tmp_path: Path) -> None:
    environment = make_virtual_environment(tmp_path / "env")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    with pytest.raises(ValidationError) as info:
        ensure_inside(outside / "file.txt", environment)

    assert info.value.reason is Reason.OUTSIDE_ROOT


# -- project folders are registered, never deleted -------------------------------


def test_a_poetry_project_is_a_verified_location(tmp_path: Path) -> None:
    """The registry may point the tools at a project; that is the one way to
    reach poetry, pipenv and pdm."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")

    assert is_verified_environment(project) is True


def test_a_pipenv_project_is_a_verified_location(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Pipfile").write_text("[packages]\n", encoding="utf-8")

    assert is_verified_environment(project) is True


def test_a_plain_library_folder_is_not_verified(tmp_path: Path) -> None:
    folder = tmp_path / "lib"
    folder.mkdir()
    (folder / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert is_verified_environment(folder) is False


def test_a_project_is_never_deletable(tmp_path: Path) -> None:
    """A project is the user's source code. "Forget" only edits the list;
    the delete affordance does not exist for it, and this guard is why."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")

    with pytest.raises(ValidationError) as info:
        ensure_deletable_environment(project)

    assert info.value.reason is Reason.FORBIDDEN_TARGET
    assert "project folder" in str(info.value)


def test_a_symlink_out_of_the_environment_is_refused(tmp_path: Path) -> None:
    environment = make_virtual_environment(tmp_path / "env")
    outside = tmp_path / "outside"
    outside.mkdir()

    link = environment / "escape"

    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - unprivileged Windows
        pytest.skip("this platform does not allow creating symlinks here")

    with pytest.raises(ValidationError) as info:
        ensure_inside(link, environment)

    assert info.value.reason is Reason.OUTSIDE_ROOT


def test_a_traversal_sequence_never_escapes(tmp_path: Path) -> None:
    environment = make_virtual_environment(tmp_path / "env")
    sneaky = environment / ".." / ".." / "etc"

    with pytest.raises(ValidationError):
        ensure_inside(sneaky, environment)
