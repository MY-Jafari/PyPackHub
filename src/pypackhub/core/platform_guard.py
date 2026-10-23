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
    "is_frozen_build",
    "is_windows_app_alias_stub",
    "modification_policy",
    "standard_interpreter_prefix",
    "standard_python_prefixes",
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
    PROJECT = "project"
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


#: A project whose dependency list a pip-family tool owns. Each entry pairs
#: the lock file that only that tool writes with the ``[tool.*]`` section its
#: users put in ``pyproject.toml``; either one alone proves the project, and
#: the order decides when two tools could both claim the same folder.
_POETRY_MARKERS: Final = ("poetry.lock", "[tool.poetry]")
_PDM_MARKERS: Final = ("pdm.lock", "[tool.pdm]")

#: How much of a ``pyproject.toml`` is read while classifying a folder. The
#: tool sections a project declares live near the top by convention, and a
#: classifier that reads a whole file on every scan pays for a file that can
#: be arbitrarily large.
_PROJECT_MARKER_SCAN_BYTES: Final = 65536


def project_tool(path: Path) -> str:
    """Which project tool owns *path*: ``"poetry"``, ``"pdm"``, ``"pipenv"`` or "".

    A ``Pipfile`` means pipenv. A ``pyproject.toml`` means a managed project
    only when it carries poetry's or pdm's own markers - a plain ``pyproject.toml``
    is a library or an app, and neither poetry, pipenv nor pdm would know what
    to do with it, so it stays unclassified and the folder is not an
    environment. An environment marker (``pyvenv.cfg``, ``conda-meta``) always
    wins over a project marker, because the venv inside a project is a
    different location than the project itself.
    """
    resolved = Path(path)

    if (resolved / "Pipfile").is_file():
        return "pipenv"

    pyproject = resolved / "pyproject.toml"
    if not pyproject.is_file():
        return ""

    try:
        text = pyproject.read_bytes()[:_PROJECT_MARKER_SCAN_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return ""

    for marker in _POETRY_MARKERS:
        if marker in text or (resolved / marker).is_file():
            return "poetry"
    for marker in _PDM_MARKERS:
        if marker in text or (resolved / marker).is_file():
            return "pdm"
    return ""


def detect_kind(prefix: Path) -> EnvironmentKind:
    """Classify a prefix by the markers it carries."""

    if (prefix / "pyvenv.cfg").is_file():
        if ".pyenv" in prefix.parts:
            return EnvironmentKind.PYBENV
        return EnvironmentKind.VENV

    if (prefix / "conda-meta").is_dir():
        return EnvironmentKind.CONDA

    if project_tool(prefix):
        return EnvironmentKind.PROJECT

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
    """The prefix of the interpreter this application is running under.

    A frozen build is its own runtime rather than a Python installation:
    its ``sys.executable`` is the application and its ``sys.prefix`` is
    the bundle's internal folder, neither of which any package could be
    installed with. The honest answer there is the best real Python on
    the machine; only a machine with no Python at all falls back to the
    bundle folder, because every caller needs *some* path and nothing can
    be listed for it anyway.
    """

    if is_frozen_build():
        discovered = standard_interpreter_prefix()
        if discovered is not None:
            return discovered

    return Path(sys.prefix).resolve()


def is_frozen_build() -> bool:
    """Whether this application is the PyInstaller bundle, not a Python.

    PyInstaller sets ``sys.frozen``; every piece of code that would
    otherwise trust ``sys.executable`` or ``sys.prefix`` as a Python
    interpreter has to ask this first, because in the bundle both point
    at the application itself - the one prefix that must never be offered
    to the user as an environment.
    """

    return bool(getattr(sys, "frozen", False))


#: The names conda's usual installers give their base directories. A base
#: directory is itself an environment (it carries ``conda-meta``), which is
#: why the prefixes are offered directly rather than only as parents.
_CONDA_BASE_NAMES: Final = ("anaconda3", "miniconda3", "miniforge3", "mambaforge")


def _conda_base_prefixes() -> list[Path]:
    """The conda installs this machine plausibly carries, bases only.

    Nothing is spawned to ask conda where it lives: the installers'
    documented locations are checked on disk, and the ones that are not
    there cost one ``stat`` each. All-users installs land under
    ``ProgramData`` on Windows, per-user ones under the home directory on
    every platform.
    """

    home = Path.home()
    bases = [home / name for name in _CONDA_BASE_NAMES]

    program_data = os.environ.get("PROGRAMDATA", "")
    if program_data:
        bases.extend(Path(program_data) / name for name in _CONDA_BASE_NAMES)

    # The all-users locations conda's installers offer on Linux and macOS.
    bases.extend(
        Path(f"/opt/{name}") for name in ("conda", "anaconda3", "miniconda3", "miniforge3")
    )
    return [base for base in bases if base.is_dir()]


def standard_python_prefixes() -> tuple[Path, ...]:
    """Where a real Python is installed on this machine, best first.

    Pure layout knowledge - globs and ``stat`` calls, nothing spawned - so
    it is cheap enough to call anywhere. The order is this module's answer
    to "which Python is *the* Python here", and it is deliberate: an
    all-users install before a per-user one, the python.org framework
    before the package manager's, conda's bases before the interpreter
    the operating system itself ships.

    The prefixes are offered *directly* - not through the search roots the
    discovery walks - because their parents are too big to scan (all of
    ``Program Files``, all of ``/usr``) and a conda base lives at the top
    of its install rather than inside a versions directory.
    """

    candidates: list[Path] = []

    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES", "")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
        local_programs = os.environ.get("LOCALAPPDATA", "")
        if program_files:
            candidates.extend(sorted(Path(program_files).glob("Python*")))
        if program_files_x86:
            candidates.extend(sorted(Path(program_files_x86).glob("Python*")))
        if local_programs:
            candidates.extend(
                sorted((Path(local_programs) / "Programs" / "Python").glob("Python*"))
            )
        candidates.extend(_conda_base_prefixes())
    elif sys.platform == "darwin":
        candidates.extend(
            sorted(Path("/Library/Frameworks/Python.framework/Versions").glob("[0-9]*"))
        )
        candidates.extend(_conda_base_prefixes())
        for homebrew in ("/opt/homebrew", "/usr/local"):
            if Path(homebrew).is_dir():
                candidates.append(Path(homebrew))
    else:
        candidates.extend(_conda_base_prefixes())
        candidates.append(Path("/usr/local"))

    # The interpreter the distribution ships comes last on every POSIX
    # platform: it exists on almost all of them, but it is the one with
    # the strictest write policy (PEP 668) and the least likely to be the
    # environment the user actually means.
    if os.name == "posix":
        candidates.append(Path("/usr"))

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - a broken mount answers here
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return tuple(unique)


def standard_interpreter_prefix() -> Path | None:
    """The best real Python prefix on this machine, or ``None``.

    "Best" is the first of :func:`standard_python_prefixes` that actually
    carries an interpreter, so a frozen build - whose own ``sys.prefix``
    is its runtime folder - has a truthful answer to "which Python is
    the Python here" without spawning anything.
    """

    for prefix in standard_python_prefixes():
        if _find_executable(prefix, None) is not None:
            return prefix
    return None


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

    if facts.kind is EnvironmentKind.PROJECT:
        return ModificationPolicy(
            allowed=True,
            requires_confirmation=True,
            severity=Severity.OK,
            reason_code="target.project",
            explanation=(
                "This is a project folder. Its own tool (poetry, pipenv or pdm) "
                "records every change in the project's files, so the whole team "
                "sees what was added, not just this machine."
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
