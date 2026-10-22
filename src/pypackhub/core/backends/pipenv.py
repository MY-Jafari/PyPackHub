"""The pipenv backend for PyPackHub.

pipenv is a *project* tool: it finds the project to change by walking up from
the current directory looking for a ``Pipfile``, so this backend's target is
a project folder and every plan runs there (see
:attr:`CommandPlan.cwd`). The one command pipenv has that pip does not is
``pipenv run``: it executes a command inside the project's own virtual
environment - the environment the ``Pipfile`` describes - which is why the
listing goes through ``pipenv run pip list --json``. The report is pip's own
JSON, so it is parsed by pip's parser: one shape, one reader, and the merged
listing treats pipenv's answer exactly like any pip listing.

What "install" means here is more than pip's meaning and is stated plainly:
``pipenv install`` updates the ``Pipfile`` and the lock file and then
installs, so the project's files are the record of the change. The
confirmation dialog shows that exact command, and a pipenv user reading it
knows what it does to their lock file.

``--skip-lock`` is deliberately absent: locking is the point of pipenv, and
quietly bypassing it to save seconds would change what the confirmed command
means.
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
    UnsupportedActionError,
    UnsupportedBackendError,
)
from pypackhub.core.backends.pip import _parse_pip_list
from pypackhub.core.platform_guard import InterpreterFacts, project_tool
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["PipenvBackend", "find_pipenv"]

_LOGGER = logging.getLogger(__name__)


def find_pipenv() -> Path:
    """Return the pipenv executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable("pipenv"))
    except ExecutableNotFoundError:
        _LOGGER.debug("pipenv is not resolvable as an executable")
        raise UnsupportedBackendError("pipenv could not be found") from None


class PipenvBackend(Backend):
    """A pipenv backend bound to one project folder."""

    def __init__(self, *, executable: Path | None = None) -> None:
        self._executable = executable

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="pipenv",
            can_list=True,
            can_install=True,
            can_uninstall=True,
            can_upgrade=True,
            can_downgrade=True,
            can_upgrade_all=True,
            can_search=False,
            can_show_sizes=False,
            can_install_user=False,
            need_confirmation=True,
        )

    @property
    def executable_name(self) -> str:
        return "pipenv"

    def executable_path(self) -> Path:
        if self._executable is not None:
            return self._executable
        try:
            return find_pipenv()
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("pipenv is not available on this system") from error

    def supports_environment(self, path: Path) -> bool:
        """pipenv drives a Pipfile project; an environment prefix is not one."""
        return project_tool(path) == "pipenv"

    def needs_elevation(self, action: PackageAction, prefix: Path) -> bool:
        """pipenv writes the project's files and its venv, not the prefix.

        The venv lives in pipenv's own store, so the place whose writability
        this command depends on is the project folder the action names - the
        one location the confirmation showed.
        """
        del prefix
        from pypackhub.core.metadata import directory_writable

        return not directory_writable(action.environment)

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the pipenv argument list for a validated action.

        Upgrading one package is ``pipenv update <name>`` - pipenv's own
        documented way to resolve a newer version *and* record it - while
        upgrading everything is a bare ``pipenv update``, which re-resolves
        the lock file. Downgrading is an install of an exact pin, the same
        shape pip's downgrade takes. pipenv has no ``--`` separator; the core
        validators refuse option-shaped names and specs before a plan exists.
        """
        self._require(action)
        if action.user:
            raise UnsupportedActionError(
                "pipenv installs into the project's environment only; "
                "use pip for user-site installs"
            )

        executable = str(self._executable or self.executable_path())

        if action.kind is ActionKind.UNINSTALL:
            argv = [executable, "uninstall", *action.names]
        elif action.kind is ActionKind.INSTALL:
            argv = [executable, "install", *action.specs]
        elif action.kind is ActionKind.UPGRADE:
            argv = [executable, "update", *action.names]
        elif action.kind is ActionKind.DOWNGRADE:
            argv = [executable, "install", *self._pinned_specs(action)]
        else:
            argv = [executable, "update"]

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"pipenv {action.kind.value}",
            backend="pipenv",
            cwd=action.environment,
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List the project's installed packages: ``pipenv run pip list --json``."""
        executable = str(self._executable or self.executable_path())
        try:
            result = run(
                [
                    executable,
                    "run",
                    "pip",
                    "list",
                    "--format",
                    "json",
                ],
                timeout=120.0,
                cancel=cancel,
                cwd=environment,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="pipenv",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="pipenv",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages = tuple(
            InstalledPackage(
                name=entry.get("name", "").strip(),
                specifier=entry.get("version", "").strip(),
                location=environment,
                backend="pipenv",
            )
            for entry in _parse_pip_list(result.stdout)
            if entry.get("name")
        )

        return PackageListing(
            environment=environment,
            backend="pipenv",
            packages=packages,
            cached=False,
        )
