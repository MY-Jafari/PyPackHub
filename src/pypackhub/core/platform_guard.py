"""Decide what may be done to an interpreter, before anything is done to it.

This module is pure inspection: it looks at a prefix on disk and answers three
questions (SPEC sections 11.5 and 6):

1. what kind of thing is this - a virtual environment, a conda environment, a
   pyenv version, or a base interpreter?
2. does it declare itself externally managed (PEP 668), meaning pip will refuse
   to install into it?
3. given the answers, is modifying it allowed, does it need a confirmation, and
   what should the UI suggest instead?

Nothing here spawns a process, so it can be used from a worker thread before a
job is queued to decide whether the job may run at all.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

__all__ = [
    "EnvironmentKind",
    "InterpreterFacts",
    "ModificationPolicy",
    "Severity",
    "current_prefix",
    "detect_kind",
    "find_externally_managed_marker",
    "inspect",
    "is_windows_app_alias_stub",
    "modification_policy",
]

#: Directory Windows uses for the zero-byte ``python.exe`` stubs that only open
#: the Microsoft Store. They must never be treated as interpreters.
_WINDOWS_ALIAS_DIRECTORY: Final = "windowsapps"

#: Names a real CPython installation always has at the top of its prefix.
_INTERPRETER_MARKERS: Final = ("python.exe", "python3.exe", "python")

EXTERNALLY_MANAGED_MARKER: Final = "EXTERNALLY-MANAGED"


class EnvironmentKind(str, Enum):
    """What a Python prefix on disk actually is."""

    VENV = "venv"
    CONDA = "conda"
    PYBENV = "pyenv"
    SYSTEM = "system"


class Severity(str, Enum):
    """How loudly the UI should present a decision."""

    OK = "ok"
    WARNING = "warning"
    DANGER = "danger"


@dataclass(frozen=True, slots=True)
class InterpreterFacts:
    """What was found on disk for one interpreter prefix."""

    prefix: Path
    executable: Path | None
    kind: EnvironmentKind
    externally_managed: bool
    externally_managed_marker: Path | None
    is_alias_stub: bool

    @property
    def is_isolated(self) -> bool:
        """True when installing into this prefix cannot touch a shared install."""

        isolated_kinds = {EnvironmentKind.VENV, EnvironmentKind.CONDA, EnvironmentKind.PYBENV}

        return self.kind in isolated_kinds and not self.externally_managed


@dataclass(frozen=True, slots=True)
class ModificationPolicy:
    """The verdict for one intended modification.

    ``reason_code`` is a stable identifier, not a sentence: the UI turns it into
    translated text in section 3. ``explanation`` is the untranslated fallback
    used by the CLI and by tests.
    """

    allowed: bool
    requires_confirmation: bool
    severity: Severity
    reason_code: str
    explanation: str
    suggestion: str | None = None
    would_need_installer_override: bool = False


def is_windows_app_alias_stub(path: Path) -> bool:
    """Detect the fake ``python.exe`` stubs Microsoft ships in ``WindowsApps``.

    They are zero bytes, they live under a directory named ``WindowsApps``, and
    running one opens the Store instead of a Python prompt. The spec calls them
    out by name because treating them as interpreters is a classic way to end up
    with a UI full of nonsense. The check always answers ``False`` on POSIX.
    """

    if os.name != "nt":
        return False

    parts = [part.lower() for part in path.parts]

    if _WINDOWS_ALIAS_DIRECTORY not in parts:
        return False

    try:
        return path.stat().st_size == 0
    except OSError:
        # A path we cannot stat is handled by the caller's existence checks;
        # it is not an alias stub.
        return False


def detect_kind(prefix: Path) -> EnvironmentKind:
    """Classify a prefix by the markers it carries."""

    if (prefix / "pyvenv.cfg").is_file():
        if ".pyenv" in prefix.parts:
            return EnvironmentKind.PYBENV
        return EnvironmentKind.VENV

    if (prefix / "conda-meta").is_dir():
        return EnvironmentKind.CONDA

    return EnvironmentKind.SYSTEM


def find_externally_managed_marker(prefix: Path) -> Path | None:
    """Locate the PEP 668 marker inside a prefix, if there is one.

    The marker lives in the interpreter's standard library directory, which is
    ``<prefix>/lib/python3.X`` on POSIX and ``<prefix>/Lib`` on Windows.
    """

    candidates = [prefix / "Lib" / EXTERNALLY_MANAGED_MARKER]
    candidates.extend(sorted(prefix.glob(f"lib/python*/{EXTERNALLY_MANAGED_MARKER}")))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def _find_executable(prefix: Path, executable: Path | None) -> Path | None:
    if executable is not None:
        return executable

    for name in _INTERPRETER_MARKERS:
        for directory in ("bin", "Scripts", ""):
            candidate = prefix / directory / name if directory else prefix / name
            if candidate.is_file():
                return candidate

    return None


def inspect(prefix: Path, *, executable: Path | None = None) -> InterpreterFacts:
    """Collect the facts about one interpreter prefix.

    The prefix is resolved first, so a symlinked virtual environment is judged
    by what it really points at rather than by the name it was reached through.
    """

    resolved = Path(prefix).expanduser().resolve()
    found_executable = _find_executable(resolved, executable)
    marker = find_externally_managed_marker(resolved)

    return InterpreterFacts(
        prefix=resolved,
        executable=found_executable,
        kind=detect_kind(resolved),
        externally_managed=marker is not None,
        externally_managed_marker=marker,
        is_alias_stub=found_executable is not None and is_windows_app_alias_stub(found_executable),
    )


def current_prefix() -> Path:
    """The prefix of the interpreter this application is running under."""

    return Path(sys.prefix).resolve()


def modification_policy(facts: InterpreterFacts, *, override: bool = False) -> ModificationPolicy:
    """Decide whether a changing action may target this interpreter.

    ``override`` means the user explicitly acknowledged a warning for this one
    action. It never turns a hard refusal into an approval, and it is never
    used implicitly: the caller has to have asked the user first.
    """

    if facts.is_alias_stub:
        return ModificationPolicy(
            allowed=False,
            requires_confirmation=True,
            severity=Severity.DANGER,
            reason_code="target.alias_stub",
            explanation=(
                "This is a Windows App Execution Alias stub, not a Python installation. "
                "It cannot install packages."
            ),
            suggestion="Choose a real interpreter, or install Python from python.org.",
        )

    if facts.externally_managed:
        return ModificationPolicy(
            allowed=override,
            requires_confirmation=True,
            severity=Severity.DANGER,
            reason_code="target.externally_managed",
            explanation=(
                "This interpreter is managed by the operating system and declares itself "
                "externally managed (PEP 668), so pip refuses to install into it. "
                "PyPackHub will not pass --break-system-packages on your behalf."
            ),
            suggestion="Create a virtual environment and install there instead.",
            would_need_installer_override=True,
        )

    if facts.kind is EnvironmentKind.SYSTEM:
        return ModificationPolicy(
            allowed=override,
            requires_confirmation=True,
            severity=Severity.WARNING,
            reason_code="target.base_interpreter",
            explanation=(
                "This is a base interpreter, not a virtual environment. Packages installed "
                "here are visible to every project that uses it."
            ),
            suggestion="Create a virtual environment to keep this project's packages separate.",
        )

    if facts.kind is EnvironmentKind.PYBENV:
        return ModificationPolicy(
            allowed=True,
            requires_confirmation=True,
            severity=Severity.WARNING,
            reason_code="target.pyenv",
            explanation=(
                "This environment lives inside pyenv's versions directory. It is isolated, "
                "but pyenv also owns the folder name, so renaming it outside pyenv will "
                "confuse the tool."
            ),
            suggestion=None,
        )

    return ModificationPolicy(
        allowed=True,
        requires_confirmation=True,
        severity=Severity.OK,
        reason_code="target.isolated_environment",
        explanation="This environment is isolated; installing here cannot affect other projects.",
        suggestion=None,
    )
