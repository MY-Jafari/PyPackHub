"""The conda and mamba backends for PyPackHub.

Both tools speak the same command language - mamba is a drop-in replacement
for conda's command line - so one implementation carries both, and the
backend's capability name says which binary the plans will run. That name is
also what the settings page forces and what the confirmation dialog shows, so
a user who asked for mamba sees ``mamba install ...``, not a conda command
they did not choose.

Conda is prefix-based like a virtual environment, and a conda environment is
already a first-class location in this application (``conda-meta`` is one of
the environment markers), so the commands target the environment explicitly
with ``--prefix``. They never rely on which environment happens to be
"activated": the runner inherits no such state, and an explicit target is the
only way the command in the confirmation can be the command that runs.

Unlike pip and uv, conda has no ``--`` separator documented for its package
arguments. The defence against a name being read as an option is the same
one pip's validators provide before any plan exists - names and specs are
refused if they start with ``-`` - so nothing option-shaped can reach the
argument list. The ``--`` is belt-and-braces where the tool offers it and is
absent here because conda does not.

One honest consequence, documented in the capability rather than hidden:
conda packages ship no ``RECORD`` of their installed files, so the rows they
contribute read "unknown" in the size column - the truth about them - while
pip-installed distributions in the same environment measure normally.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

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
from pypackhub.core.platform_guard import (
    EnvironmentKind,
    InterpreterFacts,
    detect_kind,
)
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["CondaBackend", "find_conda"]

_LOGGER = logging.getLogger(__name__)


def find_conda(tool: str = "conda") -> Path:
    """Return the conda-family executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable(tool))
    except ExecutableNotFoundError:
        _LOGGER.debug("%s is not resolvable as an executable", tool)
        raise UnsupportedBackendError(f"{tool} could not be found") from None


class CondaBackend(Backend):
    """One backend for both conda and mamba, driving conda environments.

    ``tool`` is the binary the plans run - ``"conda"`` or ``"mamba"`` - and the
    capability name follows it, so every surface that names the backend names
    the binary the user will see run.
    """

    def __init__(self, *, tool: str = "conda", executable: Path | None = None) -> None:
        if tool not in ("conda", "mamba"):
            raise ValueError(f"not a conda-family tool: {tool!r}")
        self._tool = tool
        self._executable = executable

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name=self._tool,
            can_list=True,
            can_install=True,
            can_uninstall=True,
            can_upgrade=True,
            can_downgrade=True,
            can_upgrade_all=True,
            can_search=False,
            # The size column measures distributions through their RECORD in
            # the prefix, so it is honest for a conda environment too: rows
            # whose package shipped no RECORD read "unknown", which is the
            # truth about them.
            can_show_sizes=True,
            can_install_user=False,
            need_confirmation=True,
        )

    @property
    def executable_name(self) -> str:
        return self._tool

    def executable_path(self) -> Path:
        if self._executable is not None:
            return self._executable
        try:
            return find_conda(self._tool)
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError(
                f"{self._tool} is not available on this system"
            ) from error

    def supports_environment(self, path: Path) -> bool:
        """Conda drives conda environments; a venv has no conda metadata."""
        return detect_kind(path) is EnvironmentKind.CONDA

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the conda argument list for a validated action.

        ``--prefix`` names the environment, ``--yes`` answers conda's
        confirmation prompt so the batch never hangs on stdin (the runner
        gives it none), and every package argument is positional. There is no
        ``--user``: conda has no user-site concept to honour.
        """
        self._require(action)
        if action.user:
            # conda has no user-site concept: the flag would be an error from
            # conda itself one confirmation too late, so it is refused here
            # with a message that says what to do instead.
            raise UnsupportedActionError(
                f"{self._tool} installs into the environment only; "
                "use pip for user-site installs"
            )

        executable = str(self._executable or self.executable_path())
        target = ["--prefix", str(action.environment)]

        if action.kind is ActionKind.UNINSTALL:
            argv = [executable, "remove", *target, "--yes", *action.names]
        elif action.kind is ActionKind.INSTALL:
            argv = [executable, "install", *target, "--yes", *action.specs]
        elif action.kind is ActionKind.UPGRADE:
            argv = [executable, "update", *target, "--yes", *action.names]
        elif action.kind is ActionKind.DOWNGRADE:
            argv = [executable, "install", *target, "--yes", *self._pinned_specs(action)]
        else:
            argv = [executable, "update", *target, "--yes", "--all"]

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"{self._tool} {action.kind.value}",
            backend=self._tool,
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List installed packages using ``conda list --json``.

        The JSON report is the whole environment - conda-installed packages
        *and* the ones pip installed into it (their channel is ``pypi``), so
        the merged listing gets conda's own view and never misses a package
        because pip could not see conda's metadata.
        """
        executable = str(self._executable or self.executable_path())
        try:
            result = run(
                [executable, "list", "--prefix", str(environment), "--json"],
                timeout=120.0,
                cancel=cancel,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend=self._tool,
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend=self._tool,
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages = tuple(
            InstalledPackage(
                name=entry.get("name", "").strip(),
                specifier=entry.get("version", "").strip(),
                location=environment,
                backend=self._tool,
            )
            for entry in _parse_conda_list(result.stdout)
            if entry.get("name")
        )

        return PackageListing(
            environment=environment,
            backend=self._tool,
            packages=packages,
            cached=False,
        )


def _parse_conda_list(stdout: str) -> list[dict[str, Any]]:
    import json

    try:
        payload = json.loads(stdout)
    except ValueError:
        return []

    if not isinstance(payload, list):
        return []

    return [entry for entry in payload if isinstance(entry, dict)]
