"""The pip backend for PyPackHub.

This backend talks to one pip installation through
:mod:`pypackhub.core.runner`. It is intentionally narrow in section 2: listing
installed packages is the part Phase 1 needs, and the rest (install, uninstall,
upgrade, freeze, requirements) is the scaffolding for the views that land in
section 4.

A pip backend is created for a specific interpreter or environment so it can
run the right pip without guessing from the search path.
"""

from __future__ import annotations

from pathlib import Path

from pypackhub.core.backends.base import (
    Backend,
    BackendCapability,
    InstalledPackage,
    PackageListing,
    UnsupportedBackendError,
)
from pypackhub.core.platform_guard import InterpreterFacts
from pypackhub.core.runner import CommandFailedError, run

__all__ = ["PipBackend", "find_pip"]


def find_pip(prefix: Path | None = None) -> Path:
    """Return the pip executable that belongs to the given prefix, if there is one."""
    from pypackhub.core.runner import resolve_executable

    if prefix is not None:
        candidates = [
            prefix / "bin" / "pip",
            prefix / "bin" / "pip3",
            prefix / "Scripts" / "pip.exe",
            prefix / "Scripts" / "pip3.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate

    # Fall back to whichever pip the user can run, preferring a version-aware
    # name before the bare one.
    for name in "pip3", "pip":
        try:
            return Path(resolve_executable(name))
        except Exception:
            continue

    raise UnsupportedBackendError("pip could not be found")


class PipBackend(Backend):
    """A pip backend bound to one interpreter or virtual environment."""

    def __init__(self, *, executable: Path | None = None, prefix: Path | None = None) -> None:
        self._executable = executable
        self._prefix = prefix

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="pip",
            can_list=True,
            can_install=True,
            can_uninstall=True,
            can_upgrade=True,
            can_search=False,
            can_show_sizes=True,
            need_confirmation=True,
        )

    @property
    def executable_name(self) -> str:
        return "pip"

    def executable_path(self) -> Path:
        try:
            return find_pip(self._prefix)
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("pip is not available on this system") from error

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
    ) -> PackageListing:
        """List installed packages for one environment using ``pip list --format=json``."""
        executable = self._executable or self.executable_path()

        try:
            result = run(
                [str(executable), "list", "--format=json"],
                timeout=120.0,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="pip",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="pip",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages = tuple(
            InstalledPackage(
                name=entry.get("name", "").strip(),
                specifier=entry.get("version", "").strip(),
                location=environment,
                backend="pip",
            )
            for entry in _parse_pip_list(result.stdout)
            if entry.get("name")
        )

        return PackageListing(
            environment=environment,
            backend="pip",
            packages=packages,
            cached=False,
        )


def _parse_pip_list(stdout: str) -> list[dict[str, str]]:
    import json

    try:
        payload = json.loads(stdout)
    except ValueError:
        return []

    if not isinstance(payload, list):
        return []

    return [entry for entry in payload if isinstance(entry, dict)]
