"""Environment detection and system information for PyPackHub.

This module turns a machine and a set of folders into a list of Python
environments PyPackHub can talk about, plus the bits of system information the
UI may want to show on a dashboard or an about panel.

Nothing here is a GUI concept: it returns plain data objects that a UI layer
can render however it wants. The detection logic is intentionally conservative
so an unrelated folder is never mistaken for an environment.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
import typing
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from pypackhub.core.platform_guard import (
    EnvironmentKind,
    InterpreterFacts,
    inspect,
)
from pypackhub.core.runner import CommandResult, run
from pypackhub.core.validation import is_verified_environment

__all__ = [
    "CreateEnvironmentError",
    "CreateEnvironmentResult",
    "EnvironmentListing",
    "EnvironmentSource",
    "SystemInfo",
    "create_environment",
    "detected_environments",
    "infer_purpose",
    "system_info",
]

#: Directories that are worth scanning for environments, in the order they are
#: worth reporting. A user-configurable root would be added later by the
#: settings layer; this is the conservative built-in default.
DEFAULT_SEARCH_ROOTS: Final = (
    Path.home() / ".local" / "share" / "virtualenvs",
    Path.home() / ".pyenv" / "versions",
    Path.home() / ".conda" / "envs",
    Path.home() / "venvs",
)


def _default_search_roots() -> tuple[Path, ...]:
    """Return DEFAULT_SEARCH_ROOTS, keeping only the ones that exist on disk."""
    return tuple(root for root in DEFAULT_SEARCH_ROOTS if root.is_dir())


class EnvironmentSource(Enum):
    """How an environment was discovered, so the UI can explain where it came from."""

    SCAN = "scan"
    EXPLICIT = "explicit"
    RUNNING = "running"


@dataclass(slots=True)
class ListedEnvironment:
    """One environment the application knows about."""

    prefix: Path
    facts: InterpreterFacts
    source: EnvironmentSource
    #: When this environment was first seen, in wall-clock seconds. "Now" is the
    #: honest default for an environment discovered in this pass.
    first_seen: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class EnvironmentListing:
    """A snapshot of the environments available on this machine."""

    environments: tuple[ListedEnvironment, ...]
    scanned_roots: tuple[Path, ...]
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Facts about the machine this application is running on."""

    platform: str
    platform_release: str
    machine: str
    python_version: str
    python_implementation: str
    running_under_prefix: Path
    executable: Path
    default_encoding: str
    locale: str


def system_info() -> SystemInfo:
    """Collect the stable bits of system information the UI may want to show."""
    try:
        import platform
    except ImportError:  # pragma: no cover - stdlib, kept for readability
        platform = None  # type: ignore[assignment]

    return SystemInfo(
        platform=(platform.system() if platform else os.name),
        platform_release=(platform.release() if platform else ""),
        machine=(platform.machine() if platform else ""),
        python_version=sys.version.splitlines()[0].strip(),
        python_implementation=sys.implementation.name,
        running_under_prefix=Path(sys.prefix).resolve(),
        executable=Path(sys.executable).resolve(),
        default_encoding=(sys.getdefaultencoding() or "utf-8"),
        locale=(os.environ.get("LANG") or os.environ.get("LC_ALL") or ""),
    )


#: A label the created-environment dialog shows while the job is running.
_CREATE_ENV_PROGRESS = "creating virtual environment\u2026"


@dataclass(frozen=True, slots=True)
class CreateEnvironmentResult:
    """What :func:`create_environment` reports after a creation attempt.

    The result is always returned, never raised: a cancelled or failed creation
    still gives the UI enough information to explain what happened.
    """

    prefix: Path
    requested_location: Path
    interpreter: Path | None
    success: bool
    result: CommandResult
    message: str


class CreateEnvironmentError(RuntimeError):
    """Base class for creation failures that are not a process error."""


def create_environment(
    location: Path,
    *,
    interpreter: Path | None = None,
    cancel: object | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> CreateEnvironmentResult:
    """Create a virtual environment at *location* using the requested interpreter.

    The environment is created with ``python -m venv --clear`` so a re-run over
    an existing folder either replaces it or fails loudly rather than layering
    state on state. The child is spawned through the hardened runner so it gets
    a sanitized environment, an isolated process group, and a timeout.

    *location* is normalized and resolved before anything is touched, and the
    parent must exist - the caller is responsible for pointing at a sensible
    folder (the settings dialog suggests ``.venv`` next to a project).

    When *interpreter* is ``None`` the running interpreter is used. When it is
    given it must be an absolute path or a name that resolves through the
    hardened search path (a relative path is refused).
    """
    if on_progress is not None:
        on_progress(_CREATE_ENV_PROGRESS)

    resolved_location = Path(location).expanduser().resolve()

    if not resolved_location.parent.is_dir():
        raise CreateEnvironmentError(f"parent of {resolved_location!s} does not exist")

    if interpreter is None:
        selected_interpreter = Path(sys.executable).resolve()
    else:
        # The absoluteness check has to happen on what the caller gave us, before
        # ``resolve()`` turns any path into an absolute one - otherwise a relative
        # path would be resolved against the current working directory, which is
        # exactly the accidental choice this refuses.
        candidate = Path(interpreter).expanduser()
        if not candidate.is_absolute():
            raise CreateEnvironmentError(f"interpreter path must be absolute: {interpreter!s}")
        selected_interpreter = candidate.resolve()

    # ``venv`` is part of the standard library, so the module is always present
    # on a CPython installation. Using ``-m venv`` rather than the ``venv``
    # script means the environment is always created by the interpreter the user
    # asked for, including inside a conda or pyenv environment.
    argv = (
        str(selected_interpreter),
        "-m",
        "venv",
        "--clear",
        str(resolved_location),
    )

    # The runner accepts a ``threading.Event`` directly; if the caller
    # passes a cancel token that is one, forward it so creation can be
    # cancelled the same way every other long job in the application is.
    cancel_event: threading.Event | None = None
    if cancel is not None and hasattr(cancel, "is_set"):
        try:
            cancel_event = typing.cast(threading.Event, cancel)
        except Exception:  # pragma: no cover - defensive
            cancel_event = None

    result = run(
        argv,
        timeout=600.0,
        cwd=resolved_location.parent,
        cancel=cancel_event,
        on_stdout=(on_progress or (lambda line: None)),
        on_stderr=(on_progress or (lambda line: None)),
        check=False,
    )

    if result.ok:
        message = f"Virtual environment created at {resolved_location!s}."
        if on_progress is not None:
            on_progress(message)
        return CreateEnvironmentResult(
            prefix=resolved_location,
            requested_location=location,
            interpreter=selected_interpreter,
            success=True,
            result=result,
            message=message,
        )

    # A cancelled run usually exits non-zero, so the cancel/timeout checks have to
    # come first: testing the exit code first would report every cancellation as a
    # plain failure.
    if result.cancelled:
        reason = "creation was cancelled"
    elif result.timed_out:
        reason = "creation timed out"
    else:
        reason = "creation failed"
    message = f"{reason}: {result.display_command()}"
    if on_progress is not None:
        on_progress(message)
    return CreateEnvironmentResult(
        prefix=resolved_location,
        requested_location=location,
        interpreter=selected_interpreter,
        success=False,
        result=result,
        message=message,
    )


def _candidate_roots(roots: tuple[Path, ...] | None = None) -> tuple[Path, ...]:
    if roots is None:
        roots = _default_search_roots()

    seen: list[Path] = []
    for root in roots:
        resolved = Path(root).expanduser().resolve()
        if resolved.is_dir() and resolved not in seen:
            seen.append(resolved)

    return tuple(seen)


def _is_interesting_environment(facts: InterpreterFacts) -> bool:
    """True when what was found on disk is an environment worth listing.

    A virtual environment, a conda environment or a pyenv version is always
    interesting. A plain directory is not: ``detect_kind`` answers ``SYSTEM`` for
    anything without environment markers, so accepting that unconditionally made
    every subdirectory of a scanned root look like an environment - which is
    exactly what SPEC section 2 forbids ("never treat unrelated folders as
    environments"). A system prefix therefore has to prove itself by containing
    an interpreter.
    """
    if facts.kind is not EnvironmentKind.SYSTEM:
        return True

    return facts.executable is not None and not facts.is_alias_stub


def detected_environments(
    roots: tuple[Path, ...] | None = None,
    *,
    include_running: bool = True,
    registered: Sequence[Path] = (),
) -> EnvironmentListing:
    """Discover Python environments under the given roots.

    The running interpreter is included separately so it is always present even
    when it does not live under a scanned root. Environments are returned in a
    stable order so the UI does not reshuffle them between refreshes.

    ``registered`` are the folders the user pointed the application at:
    they are offered when they are still on disk and look like an
    environment or a project, and a folder that is gone is skipped rather
    than forgotten, so it comes back if its drive does.
    """
    scanned = _candidate_roots(roots)

    found: list[ListedEnvironment] = []
    seen: set[Path] = set()

    for root in scanned:
        try:
            entries = sorted(root.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue

        for entry in entries:
            if not entry.is_dir():
                continue

            resolved = entry.resolve()
            if resolved in seen:
                continue

            try:
                facts = inspect(resolved)
            except OSError:
                continue

            if not _is_interesting_environment(facts):
                continue

            # A virtual environment whose interpreter is gone is broken, not
            # usable: listing it would offer the user a target that cannot run.
            if facts.executable is None and facts.kind is not EnvironmentKind.SYSTEM:
                continue

            seen.add(resolved)
            found.append(
                ListedEnvironment(
                    prefix=resolved,
                    facts=facts,
                    source=EnvironmentSource.SCAN,
                )
            )

    for candidate in registered:
        resolved = Path(candidate).expanduser()
        try:
            resolved = resolved.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        try:
            if not is_verified_environment(resolved):
                continue
            facts = inspect(resolved)
        except OSError:
            continue
        if facts.kind is EnvironmentKind.PROJECT and facts.executable is None:
            # A project folder is exactly the registered case with no
            # interpreter at its root - that is what makes it a project and
            # not an environment, so the "interpreter is gone" refusal for
            # environments does not apply.
            seen.add(resolved)
            found.append(
                ListedEnvironment(
                    prefix=resolved,
                    facts=facts,
                    source=EnvironmentSource.EXPLICIT,
                )
            )
            continue
        if not _is_interesting_environment(facts):
            continue
        if facts.executable is None and facts.kind is not EnvironmentKind.SYSTEM:
            continue
        seen.add(resolved)
        found.append(
            ListedEnvironment(
                prefix=resolved,
                facts=facts,
                source=EnvironmentSource.EXPLICIT,
            )
        )

    if include_running:
        running = Path(sys.prefix).resolve()
        if running not in seen:
            with contextlib.suppress(OSError):
                found.append(
                    ListedEnvironment(
                        prefix=running,
                        facts=inspect(running),
                        source=EnvironmentSource.RUNNING,
                    )
                )

    return EnvironmentListing(
        environments=tuple(found),
        scanned_roots=scanned,
    )


# -- purpose inference ---------------------------------------------------------


def _normalized(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


#: The purpose guesses the dashboard offers, keyed by the stable identifier the
#: UI translates. Each set of names votes; the purpose with the most hits wins.
#: The exact wording lives in the translation tables - the identifiers are not
#: user-facing text.
_PURPOSE_SIGNALS: Final[dict[str, frozenset[str]]] = {
    "data-science": frozenset(
        {
            "numpy",
            "pandas",
            "scipy",
            "matplotlib",
            "seaborn",
            "plotly",
            "bokeh",
            "jupyter",
            "notebook",
            "ipykernel",
            "polars",
            "pyarrow",
            "statsmodels",
            "scikit-learn",
        }
    ),
    "machine-learning": frozenset(
        {
            "torch",
            "tensorflow",
            "keras",
            "jax",
            "transformers",
            "scikit-learn",
            "xgboost",
            "lightgbm",
            "onnx",
            "datasets",
            "accelerate",
        }
    ),
    "web-api": frozenset(
        {
            "flask",
            "django",
            "fastapi",
            "starlette",
            "uvicorn",
            "gunicorn",
            "aiohttp",
            "sqlalchemy",
            "celery",
            "djangorestframework",
            "tornado",
            "bottle",
        }
    ),
    "scraping-automation": frozenset(
        {
            "scrapy",
            "selenium",
            "playwright",
            "beautifulsoup4",
            "lxml",
            "requests",
            "httpx",
            "twisted",
            "paramiko",
            "netmiko",
        }
    ),
    "gui-desktop": frozenset(
        {
            "pyside6",
            "pyqt6",
            "kivy",
            "wxpython",
            "pygobject",
            "dearpygui",
            "arcade",
            "pygame",
            "imgui",
        }
    ),
    "dev-tooling": frozenset(
        {
            "pytest",
            "black",
            "mypy",
            "ruff",
            "flake8",
            "isort",
            "pylint",
            "pre-commit",
            "coverage",
            "tox",
            "nox",
            "pip-audit",
            "bandit",
        }
    ),
}


def infer_purpose(names: Iterable[str]) -> str:
    """The most likely purpose of an environment, from the packages it has.

    Returns a stable identifier (``"data-science"``, ``"web-api"``) or
    ``"general"`` when nothing wins. Each purpose votes once per one of its
    signal packages that is installed; the most hits wins. Ties go to the
    purpose whose signal set is the better covered - two hits out of a tight
    category say more than two out of a broad one - and then to the earlier
    entry, so the same environment never guesses differently on two refreshes.
    """
    installed = {_normalized(name) for name in names}
    if not installed:
        return "general"

    best_purpose = "general"
    best_hits = 0
    best_coverage = 0.0

    for purpose, signals in _PURPOSE_SIGNALS.items():
        hits = len(signals & installed)
        if not hits:
            continue
        coverage = hits / len(signals)
        if (hits, coverage) > (best_hits, best_coverage):
            best_purpose = purpose
            best_hits = hits
            best_coverage = coverage

    return best_purpose
