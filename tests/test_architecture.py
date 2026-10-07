"""Enforce the architecture rules of SPEC section 4.

`pypackhub.core` must stay importable without a GUI toolkit: the CLI companion,
the test-suite, and CI all rely on it. This module fails the build the moment
someone adds a Qt import to the core.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "pypackhub"
CORE_ROOT = PACKAGE_ROOT / "core"

GUI_TOOLKITS = frozenset({"PySide6", "PyQt5", "PyQt6", "tkinter", "pygame"})


def _core_modules() -> list[Path]:
    return sorted(CORE_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)

    return modules


def test_core_package_exists() -> None:
    assert CORE_ROOT.is_dir()
    assert (CORE_ROOT / "__init__.py").is_file()


def test_core_contains_modules_to_check() -> None:
    """Guard against the parametrised tests below silently checking nothing."""

    assert _core_modules()


@pytest.mark.parametrize("path", _core_modules(), ids=lambda path: path.name)
def test_core_does_not_import_a_gui_toolkit(path: Path) -> None:
    offenders = {
        module for module in _imported_modules(path) if module.split(".")[0] in GUI_TOOLKITS
    }

    assert not offenders, f"{path} must not import a GUI toolkit; found {sorted(offenders)}"


@pytest.mark.parametrize("path", _core_modules(), ids=lambda path: path.name)
def test_core_does_not_import_the_ui_package(path: Path) -> None:
    offenders = {
        module
        for module in _imported_modules(path)
        if module == "pypackhub.ui" or module.startswith("pypackhub.ui.")
    }

    assert not offenders, f"{path} must not import pypackhub.ui; found {sorted(offenders)}"
