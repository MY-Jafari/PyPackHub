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
import os
import threading
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
from pypackhub.core.platform_guard import inspect as inspect_prefix
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["UvBackend", "find_uv", "uv_path_in_prefix"]

_LOGGER = logging.getLogger(__name__)


def find_uv(prefix: Path | None = None) -> Path:
    """Return the uv executable this backend would run, or raise.

    A uv installed *into the environment* - the outcome of the bootstrap flow
    that ``python -m pip install uv`` offers when uv is missing - is checked
    first, exactly like pip's own lookup: a tool that belongs to the
    environment beats whatever happens to be on the inherited search path.
    """
    from pypackhub.core.runner import resolve_executable

    if prefix is not None:
        for candidate in (prefix / "bin" / "uv", prefix / "Scripts" / "uv.exe"):
            if candidate.is_file():
                return candidate

    try:
        return Path(resolve_executable("uv"))
    except ExecutableNotFoundError:
        _LOGGER.debug("uv is not resolvable as an executable")
        raise UnsupportedBackendError("uv could not be found") from None


def uv_path_in_prefix(prefix: Path) -> Path:
    """Where a uv installed *into* this environment lives, before it exists.

    The bootstrap flow plans the chosen manager's command before that manager
    is installed; this is the path the installing command is about to fill,
    so the plan's first word is a real location rather than a bare name the
    hardened runner would refuse to resolve.
    """
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return prefix / scripts / f"uv{suffix}"


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
        if self._executable is not None:
            return self._executable
        # A bound interpreter implies its prefix: a uv that was installed
        # into this environment (the bootstrap flow's doing) is found even
        # when the environment is not on the search path.
        prefix = None
        if self._python is not None:
            prefix = self._python.parent.parent
        try:
            return find_uv(prefix)
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("uv is not available on this system") from error

    def supports_environment(self, path: Path) -> bool:
        """uv names an interpreter with ``--python``, and a project folder has none.

        The same refusal pip makes for the same reason: without an
        interpreter to name, uv would fall back to whatever Python it finds
        first and change an environment the user did not select.
        """
        if self._python is not None:
            return True
        return inspect_prefix(path).executable is not None

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

    def download_plan(self, action: PackageAction) -> CommandPlan | None:
        """uv has no ``pip download`` equivalent, so there is no pre-step.

        pip needs a download-then-install dance so it can replace itself while it
        is running; uv merges downloading into ``uv pip install`` behind a
        persistent cache and is a standalone binary that never upgrades itself
        through ``pip``, so the dance has no counterpart here. Emitting
        ``uv pip download`` would only fail with "unrecognized subcommand", so the
        install runs in a single pass.
        """
        return None

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List installed packages for one environment using ``uv pip list --format json``."""
        executable = self._executable or self.executable_path()
        target = ["--python", str(self._python)] if self._python is not None else []

        try:
            result = run(
                [str(executable), "pip", "list", "--format", "json", *target],
                timeout=120.0,
                cancel=cancel,
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
