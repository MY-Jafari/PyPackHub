"""The uv backend for PyPackHub.

This backend talks to one uv installation through
:mod:`pypackhub.core.runner`. As with the pip backend, the section 2 skeleton
is the listing path; install/uninstall/upgrade/sync are scaffolded so the rest
of the application can treat uv as a first-class package manager rather than a
pip replacement.

A uv backend is created for a specific project or Python prefix so it can run
the right uv and read the right project metadata when that matters.
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

__all__ = ["UvBackend", "find_uv"]


def find_uv() -> Path:
    """Return the uv executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    for name in "uv":
        try:
            return Path(resolve_executable(name))
        except Exception:
            continue

    raise UnsupportedBackendError("uv could not be found")


class UvBackend(Backend):
    """A uv backend bound to one Python interpreter or project."""

    def __init__(self, *, executable: Path | None = None, python: Path | None = None) -> None:
        self._executable = executable
        self._python = python

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="uv",
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
        return "uv"

    def executable_path(self) -> Path:
        try:
            return find_uv() if self._executable is None else self._executable
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("uv is not available on this system") from error

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
    ) -> PackageListing:
        """List installed packages for one environment using ``uv pip list --format json``."""
        executable = self._executable or self.executable_path()

        try:
            result = run(
                [str(executable), "pip", "list", "--format", "json"],
                timeout=120.0,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="uv",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="uv",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages = tuple(
            InstalledPackage(
                name=entry.get("name", "").strip(),
                specifier=entry.get("version", "").strip(),
                location=environment,
                backend="uv",
            )
            for entry in _parse_uv_list(result.stdout)
            if entry.get("name")
        )

        return PackageListing(
            environment=environment,
            backend="uv",
            packages=packages,
            cached=False,
        )


def _parse_uv_list(stdout: str) -> list[dict[str, str]]:
    import json

    try:
        payload = json.loads(stdout)
    except ValueError:
        return []

    if not isinstance(payload, list):
        return []

    return [entry for entry in payload if isinstance(entry, dict)]
