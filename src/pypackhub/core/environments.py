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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from pypackhub.core.platform_guard import (
    EnvironmentKind,
    InterpreterFacts,
    detect_kind,
    inspect,
)

__all__ = [
    "EnvironmentListing",
    "EnvironmentSource",
    "SystemInfo",
    "detected_environments",
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
    first_seen: float = field(default_factory=os.path.getmtime)


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


def _candidate_roots(roots: tuple[Path, ...] | None = None) -> tuple[Path, ...]:
    if roots is None:
        roots = _default_search_roots()

    seen: list[Path] = []
    for root in roots:
        resolved = Path(root).expanduser().resolve()
        if resolved.is_dir() and resolved not in seen:
            seen.append(resolved)

    return tuple(seen)


def _is_interesting_environment(path: Path) -> bool:
    """True when a directory qualifies as an environment worth listing."""
    return detect_kind(path) in {
        EnvironmentKind.VENV,
        EnvironmentKind.CONDA,
        EnvironmentKind.PYBENV,
        EnvironmentKind.SYSTEM,
    }


def detected_environments(
    roots: tuple[Path, ...] | None = None,
    *,
    include_running: bool = True,
) -> EnvironmentListing:
    """Discover Python environments under the given roots.

    The running interpreter is included separately so it is always present even
    when it does not live under a scanned root. Environments are returned in a
    stable order so the UI does not reshuffle them between refreshes.
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

            if not _is_interesting_environment(resolved):
                continue

            try:
                facts = inspect(resolved)
            except OSError:
                continue

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
