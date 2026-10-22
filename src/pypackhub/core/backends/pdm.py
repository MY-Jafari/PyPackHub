"""The pdm backend for PyPackHub.

pdm is a *project* tool: it acts on the project whose ``pyproject.toml`` it
finds in the current directory, so this backend's target is a project folder
and every plan runs there (see :attr:`CommandPlan.cwd`). ``pdm add`` writes
the dependency into ``pyproject.toml`` and the lock file before installing -
the project's files are the record of the change, and the confirmation shows
exactly that command.

The listing parses ``pdm list`` text. pdm has no JSON report for installed
packages, and running pip through the project's environment instead (the
trick the pipenv backend uses) would break on PEP 582 projects, where
packages live in ``__pypackages__`` and no venv exists at all. ``pdm list``
is the one command that answers for every layout pdm supports, so its table
is what gets parsed: a row counts only when its first token is a package name
and its second a version. Output that parses to nothing is reported as an
error rather than shown as an empty project - "nothing installed" and "could
not read the answer" must never look the same.
"""

from __future__ import annotations

import logging
import re
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
from pypackhub.core.platform_guard import InterpreterFacts, project_tool
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["PdmBackend", "find_pdm"]

_LOGGER = logging.getLogger(__name__)

#: A package name as the core validators see it, used only to reject table
#: headers and box-drawing rules; the authoritative validation happens before
#: any plan exists. A version in the second column starts with a digit.
_NAME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION_TOKEN = re.compile(r"^\d")


def find_pdm() -> Path:
    """Return the pdm executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable("pdm"))
    except ExecutableNotFoundError:
        _LOGGER.debug("pdm is not resolvable as an executable")
        raise UnsupportedBackendError("pdm could not be found") from None


class PdmBackend(Backend):
    """A pdm backend bound to one project folder."""

    def __init__(self, *, executable: Path | None = None) -> None:
        self._executable = executable

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="pdm",
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
        return "pdm"

    def executable_path(self) -> Path:
        if self._executable is not None:
            return self._executable
        try:
            return find_pdm()
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("pdm is not available on this system") from error

    def supports_environment(self, path: Path) -> bool:
        """pdm drives a pdm project; an environment prefix is not one."""
        return project_tool(path) == "pdm"

    def needs_elevation(self, action: PackageAction, prefix: Path) -> bool:
        """pdm writes the project's files and its venv, not the prefix.

        The venv's location is pdm's own decision, so the place whose
        writability this command depends on is the project folder the action
        names - the one location the confirmation showed.
        """
        del prefix
        from pypackhub.core.metadata import directory_writable

        return not directory_writable(action.environment)

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the pdm argument list for a validated action.

        pdm's own vocabulary maps cleanly: ``pdm add`` installs and records,
        ``pdm remove`` removes and records, ``pdm update`` re-resolves named
        packages or - bare - everything. A downgrade is an add of an exact
        pin, the same shape pip's downgrade takes. pdm has no ``--``
        separator; the core validators refuse option-shaped names and specs
        before a plan exists.
        """
        self._require(action)
        if action.user:
            raise UnsupportedActionError(
                "pdm installs into the project's environment only; "
                "use pip for user-site installs"
            )

        executable = str(self._executable or self.executable_path())

        if action.kind is ActionKind.UNINSTALL:
            argv = [executable, "remove", *action.names]
        elif action.kind is ActionKind.INSTALL:
            argv = [executable, "add", *action.specs]
        elif action.kind is ActionKind.UPGRADE:
            argv = [executable, "update", *action.names]
        elif action.kind is ActionKind.DOWNGRADE:
            argv = [executable, "add", *self._pinned_specs(action)]
        else:
            argv = [executable, "update"]

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"pdm {action.kind.value}",
            backend="pdm",
            cwd=action.environment,
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List the project's installed packages using ``pdm list``."""
        executable = str(self._executable or self.executable_path())
        try:
            result = run(
                [executable, "list"],
                timeout=120.0,
                cancel=cancel,
                cwd=environment,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="pdm",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="pdm",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages, parsed_any = _parse_pdm_list(result.stdout, environment)

        if not parsed_any and result.stdout.strip():
            return PackageListing(
                environment=environment,
                backend="pdm",
                packages=(),
                error="pdm list produced no readable package rows",
            )

        return PackageListing(
            environment=environment,
            backend="pdm",
            packages=tuple(packages),
            cached=False,
        )


def _parse_pdm_list(stdout: str, environment: Path) -> tuple[list[InstalledPackage], bool]:
    """Parse pdm's list table into packages.

    Returns the packages and whether *any* row looked like one at all - the
    caller turns "output, but nothing readable" into an error instead of an
    empty listing.
    """
    packages: list[InstalledPackage] = []
    for line in stdout.splitlines():
        tokens = line.split()
        if len(tokens) < 2:
            continue
        if not _NAME_TOKEN.match(tokens[0]) or not _VERSION_TOKEN.match(tokens[1]):
            continue
        packages.append(
            InstalledPackage(
                name=tokens[0],
                specifier=tokens[1],
                location=environment,
                backend="pdm",
            )
        )
    return packages, bool(packages)
