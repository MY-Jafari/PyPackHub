"""Registered locations: the folders the user points the application at.

Discovery only looks inside a few well-known roots, which is the right
conservative default - and it leaves two whole categories unreachable: a
conda environment the user keeps somewhere custom, and a poetry / pipenv /
pdm project, which is a folder with the tool's marker files rather than an
environment at all. Registering is the answer for both, and the promise the
environments page makes about it is narrow on purpose: the folder is
*remembered*, offered in the switcher, and never deleted - forgetting it
edits the list and nothing else.

These tests pin the three sides of that promise: the discovery that offers
registered folders, the settings that remember them (including the gone
ones, so a drive that comes back brings its registration with it), and the
provider that carries them into the switcher's list.
"""

from __future__ import annotations

from pathlib import Path

from pypackhub.core.environments import (
    EnvironmentSource,
    detected_environments,
)
from pypackhub.core.settings import AppSettings, validate_settings
from pypackhub.core.validation import is_verified_environment
from pypackhub.ui.services import DefaultInventoryProvider


def _venv(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    # An interpreter file, so the "broken environment" guard does not skip it
    # for the reason it exists: a venv whose python is gone is unusable.
    (path / "python.exe").touch()
    return path


def _poetry_project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    return path


# -- discovery offers the registered folders ------------------------------------


def test_a_registered_venv_is_offered_as_explicit(tmp_path: Path) -> None:
    environment = _venv(tmp_path / "my-envs" / "work")

    listing = detected_environments((), registered=(environment,))

    assert environment in [entry.prefix for entry in listing.environments]
    entry = next(e for e in listing.environments if e.prefix == environment)
    assert entry.source is EnvironmentSource.EXPLICIT


def test_a_registered_project_is_offered_without_an_interpreter(tmp_path: Path) -> None:
    """A project has no python.exe at its root - that is what makes it a
    project - and the "broken venv" refusal for environments does not apply."""
    project = _poetry_project(tmp_path / "code" / "app")

    listing = detected_environments((), registered=(project,))

    assert project in [entry.prefix for entry in listing.environments]


def test_a_registered_folder_that_is_gone_is_skipped_not_forgotten(
    tmp_path: Path,
) -> None:
    gone = tmp_path / "unmounted-drive" / "env"

    listing = detected_environments((), registered=(gone,))

    assert gone not in [entry.prefix for entry in listing.environments]
    # The setting itself keeps the path; only the offer waits for the folder.
    assert validate_settings(
        AppSettings(registered_environments=(str(gone),))
    ).registered_environments == (str(gone),)


def test_a_registered_folder_that_is_not_verified_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert is_verified_environment(plain) is False
    listing = detected_environments((), registered=(plain,))

    assert plain not in [entry.prefix for entry in listing.environments]


def test_a_registered_folder_found_by_scan_is_not_offered_twice(tmp_path: Path) -> None:
    """A venv inside a scanned root appears once, as a scan find."""
    root = tmp_path / "roots"
    environment = _venv(root / "work")

    listing = detected_environments((root,), registered=(environment,))

    found = [entry for entry in listing.environments if entry.prefix == environment]
    assert len(found) == 1
    assert found[0].source is EnvironmentSource.SCAN


# -- the settings remember them ---------------------------------------------------


def test_registration_shape_survives_validation(tmp_path: Path) -> None:
    kept = str(_venv(tmp_path / "kept"))
    settings = validate_settings(
        AppSettings(
            registered_environments=(
                kept,
                kept,  # duplicate
                "relative/path",  # not absolute: refused
                "",  # empty: refused
                str(tmp_path / "gone"),  # absolute but absent: kept on purpose
            )
        )
    )

    assert settings.registered_environments == (
        kept,
        str(tmp_path / "gone"),
    )


def test_non_sequences_are_dropped_whole() -> None:
    settings = validate_settings(AppSettings(registered_environments="/one/path"))

    assert settings.registered_environments == ()


# -- the provider carries them into the switcher ----------------------------------


def test_the_provider_offers_registered_folders(tmp_path: Path) -> None:
    project = _poetry_project(tmp_path / "code" / "app")
    provider = DefaultInventoryProvider(
        search_working_directory=False,
        registered_environments=(project,),
    )

    assert project in provider.environments()


def test_the_provider_can_be_repointed_after_construction(tmp_path: Path) -> None:
    """Registering from the environments page updates the provider in place,
    so the switcher's next discovery includes the new folder."""
    first = _poetry_project(tmp_path / "one")
    second = _poetry_project(tmp_path / "two")
    provider = DefaultInventoryProvider(
        search_working_directory=False,
        registered_environments=(first,),
    )

    provider.set_registered_environments((first, second))

    offered = provider.environments()
    assert first in offered
    assert second in offered
