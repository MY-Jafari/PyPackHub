"""The poetry backend for PyPackHub.

poetry is a *project* tool: it finds the project to change by looking for a
``pyproject.toml`` in the current directory, so this backend's target is a
project folder, not an environment prefix. The plans therefore carry the
project as their working directory - :attr:`CommandPlan.cwd` - and the base
execution path runs them there, so ``poetry add flask`` changes *this*
project and not whichever directory the application happened to inherit.

What "install" means here is stated plainly because it is more than pip's
meaning: ``poetry add`` writes the package into ``pyproject.toml``, resolves
a new lock file and installs into the project's environment. That is the
command poetry itself offers, it is exactly what the confirmation dialog
shows, and it is what a poetry user means when they add a dependency - the
project's files are the record of the change, which is the point of using
poetry in a team.

The listing parses ``poetry show`` text rather than a JSON report because
poetry has none: each line leads with the package name and version, and
descriptions wrap onto lines the version-shaped second token exposes as
continuations. A listing that cannot be parsed is reported as an error rather
than shown as an empty project, because "nothing installed" and "could not
read the answer" must never look the same.
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

__all__ = ["PoetryBackend", "find_poetry"]

_LOGGER = logging.getLogger(__name__)

#: A poetry version in the listing's second column: it starts with a digit
#: (poetry prints no leading ``v``). Anything else in that column means the
#: line is a wrapped description, a heading or a warning - not a package.
_VERSION_TOKEN = re.compile(r"^\d")


def find_poetry() -> Path:
    """Return the poetry executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable("poetry"))
    except ExecutableNotFoundError:
        _LOGGER.debug("poetry is not resolvable as an executable")
        raise UnsupportedBackendError("poetry could not be found") from None


class PoetryBackend(Backend):
    """A poetry backend bound to one project folder."""

    def __init__(self, *, executable: Path | None = None) -> None:
        self._executable = executable

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="poetry",
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
        return "poetry"

    def executable_path(self) -> Path:
        if self._executable is not None:
            return self._executable
        try:
            return find_poetry()
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("poetry is not available on this system") from error

    def supports_environment(self, path: Path) -> bool:
        """poetry drives a poetry project; an environment prefix is not one."""
        return project_tool(path) == "poetry"

    def needs_elevation(self, action: PackageAction, prefix: Path) -> bool:
        """poetry writes the project's files and its venv, not the prefix.

        The venv's location is poetry's own decision, so the place whose
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
        """Build the poetry argument list for a validated action.

        Every plan runs inside the project the action names, so poetry
        resolves *that* project's lock file. poetry's command line has no
        ``--`` separator; the core validators already refuse option-shaped
        names and specs before a plan exists, which is the same guarantee
        pip's ``--`` provides redundantly.
        """
        self._require(action)
        if action.user:
            raise UnsupportedActionError(
                "poetry installs into the project's environment only; "
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
            label=f"poetry {action.kind.value}",
            backend="poetry",
            cwd=action.environment,
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List the project's installed packages using ``poetry show``."""
        executable = str(self._executable or self.executable_path())
        try:
            result = run(
                [executable, "show", "--no-ansi"],
                timeout=120.0,
                cancel=cancel,
                cwd=environment,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="poetry",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="poetry",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        packages, parsed_any = _parse_poetry_show(result.stdout, environment)

        if not parsed_any and result.stdout.strip():
            # Output that parses to nothing is only an empty project when it
            # really was empty; poetry warnings and errors print to stdout on
            # some versions, and showing them as "no packages" would be a lie.
            return PackageListing(
                environment=environment,
                backend="poetry",
                packages=(),
                error="poetry show produced no readable package lines",
            )

        return PackageListing(
            environment=environment,
            backend="poetry",
            packages=tuple(packages),
            cached=False,
        )


def _parse_poetry_show(stdout: str, environment: Path) -> tuple[list[InstalledPackage], bool]:
    """Parse poetry's ``show`` table into packages.

    Returns the packages and whether *any* line looked like one at all - the
    caller turns "output, but nothing readable" into an error instead of an
    empty listing. Each package's location is the project folder: that is
    where poetry's own record of it lives, and where the change would be
    written.
    """
    packages: list[InstalledPackage] = []
    for line in stdout.splitlines():
        tokens = line.split()
        if len(tokens) < 2 or not _VERSION_TOKEN.match(tokens[1]):
            continue
        packages.append(
            InstalledPackage(
                name=tokens[0],
                specifier=tokens[1],
                location=environment,
                backend="poetry",
            )
        )
    return packages, bool(packages)
