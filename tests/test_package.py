"""Distribution-level checks: the built project must describe itself correctly."""

from __future__ import annotations

from importlib.metadata import metadata, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

import pypackhub

PACKAGE_ROOT = Path(pypackhub.__file__).resolve().parent
FONT_ROOT = PACKAGE_ROOT / "assets" / "fonts"
FONT_FAMILIES = ("inter", "jetbrains-mono", "vazirmatn")


def _requirements() -> list[Requirement]:
    declared = metadata(pypackhub.APP_ID).get_all("Requires-Dist") or []

    return [Requirement(entry) for entry in declared]


def _runtime_requirements() -> dict[str, Requirement]:
    """Requirements that apply to a plain installation (no extras enabled)."""

    return {
        canonicalize_name(requirement.name): requirement
        for requirement in _requirements()
        if requirement.marker is None or requirement.marker.evaluate()
    }


def _extra_requirements(extra: str) -> dict[str, Requirement]:
    return {
        canonicalize_name(requirement.name): requirement
        for requirement in _requirements()
        if requirement.marker is not None and requirement.marker.evaluate({"extra": extra})
    }


def test_version_comes_from_distribution_metadata() -> None:
    """`__version__` is the version the build actually recorded."""

    assert pypackhub.__version__ == version(pypackhub.APP_ID)


def test_minimum_python_floor_is_declared() -> None:
    """PySide6 needs >=3.10, so the distribution must refuse anything older."""

    requires_python = metadata(pypackhub.APP_ID)["Requires-Python"]

    assert requires_python is not None
    assert ">=3.10" in requires_python


def test_runtime_dependencies_are_declared() -> None:
    """A missing runtime dependency would otherwise only surface at first launch."""

    declared = _runtime_requirements()

    for expected in ("pyside6", "httpx", "packaging", "platformdirs", "keyring"):
        assert expected in declared, expected


def test_pyside6_is_capped_below_the_next_minor() -> None:
    """Qt renames and removes APIs between minor releases; the cap must stay."""

    specifier = _runtime_requirements()["pyside6"].specifier

    assert specifier.contains("6.8")
    assert not specifier.contains("7.0")


def test_dev_extra_declares_the_toolchain_ci_runs() -> None:
    """Losing one of these from the extra would turn a CI check into a no-op."""

    declared = _extra_requirements("dev")

    for expected in (
        "pytest",
        "pytest-qt",
        "pytest-cov",
        "ruff",
        "black",
        "mypy",
        "bandit",
        "pip-audit",
    ):
        assert expected in declared, expected


def test_distribution_ships_the_typing_marker() -> None:
    """The classifiers promise `Typing :: Typed`, so `py.typed` has to exist."""

    assert (PACKAGE_ROOT / "py.typed").is_file()


def test_bundled_fonts_and_their_license_are_present() -> None:
    """The fonts are package data: packaging mistakes here break the UI, not the build."""

    for family in FONT_FAMILIES:
        directory = FONT_ROOT / family

        assert directory.is_dir(), family
        assert (directory / "OFL.txt").is_file(), family
        assert list(directory.glob("*.ttf")), family
