"""The entry points: the console script and ``python -m pypackhub``.

A typo in a ``[project.scripts]`` target is invisible until someone installs the
package and runs the command, so it is checked here instead: the target is
imported and called (with the Qt dependency skipped when it is not installed).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"


def _scripts() -> dict[str, str]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]["scripts"]


def test_console_script_is_declared() -> None:
    assert _scripts() == {"pypackhub": "pypackhub.ui.app:run"}


@pytest.mark.parametrize("target", _scripts().values())
def test_console_script_target_resolves_to_a_callable(target: str) -> None:
    pytest.importorskip("PySide6")
    module_name, _, attribute = target.partition(":")
    module = importlib.import_module(module_name)
    entry = getattr(module, attribute)
    assert callable(entry)


def test_module_entry_point_exists() -> None:
    main_module = importlib.import_module("pypackhub.__main__")
    assert callable(main_module.main)
