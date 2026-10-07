"""Shared backend interface for package-manager backends.

Every backend in this project talks to exactly one package manager through the
hardened runner in :mod:`pypackhub.core.runner`. This module is the contract:
what the rest of the application may ask of a backend without knowing whether it
is pip, uv, conda, poetry, pipx, pipenv or pdm.

This skeleton is the section 2 starting point. The pip and uv backends are the
first concrete ones to land, because they are the ones Phase 1 needs for the
packages view.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pypackhub.core.platform_guard import InterpreterFacts, ModificationPolicy
from pypackhub.core.validation import validate_package_spec

__all__ = [
    "Backend",
    "BackendCapability",
    "BackendError",
    "InstalledPackage",
    "PackageListing",
    "UnsupportedBackendError",
]


class BackendError(RuntimeError):
    """A backend command failed or cannot be run on this machine."""


class UnsupportedBackendError(BackendError):
    """The requested backend is not available on this machine."""


@dataclass(frozen=True, slots=True)
class BackendCapability:
    """What a backend can do today, without spawning anything to find out."""

    name: str
    can_list: bool = True
    can_install: bool = False
    can_uninstall: bool = False
    can_upgrade: bool = False
    can_search: bool = False
    can_show_sizes: bool = False
    need_confirmation: bool = True


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One package reported by a backend."""

    name: str
    specifier: str
    location: Path
    editable: bool = False
    direct_reference: bool = False
    backend: str = ""


@dataclass(frozen=True, slots=True)
class PackageListing:
    """The result of listing an environment through one backend."""

    environment: Path
    backend: str
    packages: tuple[InstalledPackage, ...]
    error: str = ""
    timed_out: bool = False
    cached: bool = False


class Backend(ABC):
    """One package manager PyPackHub can talk to."""

    @property
    @abstractmethod
    def capability(self) -> BackendCapability:
        ...

    @property
    @abstractmethod
    def executable_name(self) -> str:
        """The bare command name this backend looks for on the search path."""

    @abstractmethod
    def executable_path(self) -> Path:
        """The resolved executable this backend would actually run, or raises."""

    @abstractmethod
    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
    ) -> PackageListing:
        """Return the installed packages in *environment* through this backend."""

    def policy_for(self, facts: InterpreterFacts) -> ModificationPolicy:
        """Default policy delegate: most backends only need the platform guard."""
        from pypackhub.core.platform_guard import modification_policy

        return modification_policy(facts)

    def validate_package_spec(self, spec: str) -> str:
        """Reuse the core validator unless a backend needs stricter rules."""
        return validate_package_spec(spec)
