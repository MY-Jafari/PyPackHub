"""Creating a virtual environment.

Two kinds of test here. The refusals are pure logic and always run. The creation
itself is a real ``python -m venv`` child process - deliberately not mocked,
because "does this actually make an environment" is the only question that matters
and a mock cannot answer it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pypackhub.core.environments import (
    CreateEnvironmentError,
    create_environment,
)


def test_a_location_without_an_existing_parent_is_refused(tmp_path: Path) -> None:
    missing = tmp_path / "not-here" / "env"

    with pytest.raises(CreateEnvironmentError):
        create_environment(missing)


def test_a_relative_interpreter_is_refused(tmp_path: Path) -> None:
    """A relative path is refused so a file next to the project cannot be chosen.

    The check happens before anything is spawned, so this is cheap and cannot
    create a half-built environment on a misunderstanding.
    """
    with pytest.raises(CreateEnvironmentError):
        create_environment(tmp_path / "env", interpreter=Path("python"))


@pytest.mark.slow
def test_a_real_environment_is_created_and_usable(tmp_path: Path) -> None:
    location = tmp_path / "created-env"

    progress: list[str] = []
    result = create_environment(
        location,
        interpreter=Path(sys.executable),
        on_progress=progress.append,
    )

    assert result.success, result.message
    assert result.prefix == location.resolve()
    assert result.interpreter == Path(sys.executable).resolve()
    # A created environment is a directory with something to run in it. On
    # Windows the interpreter sits in ``Scripts``, everywhere else in ``bin``.
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    assert (location / scripts).is_dir()
    assert list((location / scripts).glob("python*"))
    # Progress was reported, which is what the log panel renders while it runs.
    assert progress


@pytest.mark.slow
def test_re_running_over_an_existing_folder_replaces_it(tmp_path: Path) -> None:
    location = tmp_path / "replaced-env"
    assert create_environment(location).success

    # ``--clear`` is passed, so a second run replaces the folder rather than
    # layering a new environment on top of the old one or failing outright.
    marker = location / "left-over.txt"
    marker.write_text("stale", encoding="utf-8")

    result = create_environment(location)
    assert result.success, result.message
    assert not marker.exists(), "the previous environment was not cleared"
