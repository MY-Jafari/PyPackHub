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

import logging
from pathlib import Path

from pypackhub.core.backends.base import (
    ActionKind,
    Backend,
    BackendCapability,
    CommandPlan,
    InstalledPackage,
    PackageAction,
    PackageListing,
    UnsupportedActionError,
    UnsupportedBackendError,
)
from pypackhub.core.platform_guard import InterpreterFacts
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["UvBackend", "find_uv"]

_LOGGER = logging.getLogger(__name__)


def find_uv() -> Path:
    """Return the uv executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable("uv"))
    except ExecutableNotFoundError:
        _LOGGER.debug("uv is not resolvable as an executable")
        raise UnsupportedBackendError("uv could not be found") from None


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
            can_downgrade=True,
            can_upgrade_all=True,
            can_search=False,
            can_show_sizes=True,
            # uv installs into a specific interpreter, never into the user site
            # directory: there is no --user flag to honor (SPEC section 2 asks
            # for --user support, which is why pip remains the default when a
            # user asks for it).
            can_install_user=False,
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

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the uv argument list for a validated action.

        uv is told which interpreter to act on with ``--python`` rather than by
        relying on an activated environment, so the target is always explicit and
        never depends on the shell the application inherited.
        """
        if action.user:
            raise UnsupportedActionError(
                "uv cannot install into the user site directory; use pip for --user installs"
            )

        self._require(action)
        executable = str(self._executable or self.executable_path())
        target = ["--python", str(self._python)] if self._python is not None else []

        if action.kind is ActionKind.UNINSTALL:
            argv = [executable, "pip", "uninstall", *target, "--", *action.names]
        elif action.kind is ActionKind.INSTALL:
            argv = [executable, "pip", "install", *target, "--", *action.specs]
        elif action.kind is ActionKind.UPGRADE:
            argv = [
                executable,
                "pip",
                "install",
                "--upgrade",
                *target,
                "--",
                *action.names,
            ]
        elif action.kind is ActionKind.DOWNGRADE:
            argv = [
                executable,
                "pip",
                "install",
                *target,
                "--",
                *self._pinned_specs(action),
            ]
        else:
            argv = [
                executable,
                "pip",
                "install",
                "--upgrade",
                *target,
                "--",
                *action.specs,
            ]

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"uv {action.kind.value}",
            backend="uv",
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
    ) -> PackageListing:
        """List installed packages for one environment using ``uv pip list --format json``."""
        executable = self._executable or self.executable_path()
        target = ["--python", str(self._python)] if self._python is not None else []

        try:
            result = run(
                [str(executable), "pip", "list", "--format", "json", *target],
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
