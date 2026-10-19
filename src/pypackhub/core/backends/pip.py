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

import logging
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
    UnsupportedBackendError,
)
from pypackhub.core.platform_guard import InterpreterFacts
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["PipBackend", "find_pip"]

_LOGGER = logging.getLogger(__name__)


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
    # name before the bare one. A missing candidate is the normal case, not an
    # error: it is recorded at debug level so a diagnostic can explain which
    # names were tried.
    for name in ("pip3", "pip"):
        try:
            return Path(resolve_executable(name))
        except ExecutableNotFoundError:
            _LOGGER.debug("%s is not resolvable as an executable", name)

    raise UnsupportedBackendError("pip could not be found")


class PipBackend(Backend):
    """A pip backend bound to one interpreter or virtual environment.

    When the interpreter of the target environment is known, pip is run as
    ``<interpreter> -m pip ...`` rather than by looking up a ``pip`` script on
    the search path. That matters for correctness, not just for tidiness: a bare
    ``pip`` from ``PATH`` belongs to whichever environment installed it, so
    ``pip list`` would report the wrong packages and ``pip install`` would write
    into the wrong place.
    """

    def __init__(
        self,
        *,
        executable: Path | None = None,
        prefix: Path | None = None,
        python: Path | None = None,
    ) -> None:
        self._executable = executable
        self._prefix = prefix
        self._python = python

    def _command(self, *arguments: str) -> list[str]:
        """The prefix that targets the right interpreter, then the arguments."""
        return [*self._command_prefix(), *arguments]

    def _command_prefix(self) -> list[str]:
        if self._python is not None:
            return [str(self._python), "-m", "pip"]
        return [str(self._executable or self.executable_path())]

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="pip",
            can_list=True,
            can_install=True,
            can_uninstall=True,
            can_upgrade=True,
            can_downgrade=True,
            can_upgrade_all=True,
            can_search=False,
            can_show_sizes=True,
            can_install_user=True,
            need_confirmation=True,
        )

    @property
    def executable_name(self) -> str:
        return "pip"

    def executable_path(self) -> Path:
        # With a bound interpreter, that interpreter *is* the executable this
        # backend runs. Otherwise fall back to a pip script that belongs to the
        # prefix, and only then to whatever pip the search path offers.
        if self._python is not None:
            return self._python

        try:
            return find_pip(self._prefix)
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("pip is not available on this system") from error

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the pip argument list for a validated action.

        Every command separates its options from the package arguments with
        ``--`` (SPEC section 11.1), so a name or spec can never be read as an
        option even if the validator were to let something through.
        """
        self._require(action)

        if action.kind is ActionKind.UNINSTALL:
            argv = self._command("uninstall", "--yes", "--", *action.names)
        elif action.kind is ActionKind.INSTALL:
            argv = self._command("install", *self._with_user(action, []), "--", *action.specs)
        elif action.kind is ActionKind.UPGRADE:
            argv = self._command(
                "install",
                "--upgrade",
                *self._with_user(action, []),
                "--",
                *action.names,
            )
        elif action.kind is ActionKind.DOWNGRADE:
            argv = self._command(
                "install", *self._with_user(action, []), "--", *self._pinned_specs(action)
            )
        else:
            argv = self._command(
                "install",
                "--upgrade",
                *self._with_user(action, []),
                "--",
                *action.specs,
            )

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"pip {action.kind.value}",
            backend="pip",
        )

    def download_plan(self, action: PackageAction) -> CommandPlan | None:
        """Download the targets to a cache directory without installing them.

        This is used as a pre-step for upgrades so the package manager itself can be
        upgraded without trying to replace the process that is currently running it.

        .. NOTE::

            Currently disabled. pip's ``download`` sub-command does not accept
            ``--user`` (``no such option: --user``), and the existing call passes
            ``*self._with_user(action, [])`` right before it. Until this is fixed
            (either drop ``--user``, pass ``--user`` only for the real install, or
            skip the download pre-step when ``--user`` is in play), keep this
            returning ``None`` so homes with system Python are not broken.
        """
        return None

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List installed packages for one environment using ``pip list --format=json``."""
        try:
            result = run(self._command("list", "--format=json"), timeout=120.0, cancel=cancel)
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
