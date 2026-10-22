"""The pipx backend for PyPackHub.

pipx installs command-line *applications*, each into its own throwaway
virtual environment under pipx's home directory. Nothing about it is
environment-shaped: there is no prefix to target and no interpreter to name,
so this backend's commands are the only ones in the project that ignore the
selected environment - ``pipx install black`` does the same thing no matter
which environment the application is looking at. The listing therefore
reports the pipx home as the packages' location, which is where those
applications and their venvs actually live.

The package list is what ``pipx list --json`` reports: one row per installed
application, its version taken from the venv pipx recorded. The rows are
applications, not libraries: installing from a project's requirements makes
no sense here (each app gets its own isolated venv), so the capability
advertises what pipx can genuinely do and nothing more. There is no
``--downgrade``: pinning an older version means removing and re-installing,
and inventing a one-step downgrade the tool does not offer would be a
command the confirmation showed but pipx could not honour.

The size switch is not offered either, and for a reason that is the same
everywhere the rows are not the selected environment's distributions: the
column measures the selected environment, and joining pipx's application
names against it by name could put another environment's number on a
pipx row. Unknown is honest; a wrong number is not.

pipx writes only inside the user's own home, so this backend's policy never
demands the operating system's help: an elevation prompt for a command that
cannot leave the user's directories would be consent theatre.
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
from pypackhub.core.platform_guard import (
    InterpreterFacts,
    ModificationPolicy,
    Severity,
)
from pypackhub.core.runner import CommandFailedError, ExecutableNotFoundError, run

__all__ = ["PipxBackend", "find_pipx", "pipx_home_dir"]

_LOGGER = logging.getLogger(__name__)


def find_pipx() -> Path:
    """Return the pipx executable this backend would run, or raise."""
    from pypackhub.core.runner import resolve_executable

    try:
        return Path(resolve_executable("pipx"))
    except ExecutableNotFoundError:
        _LOGGER.debug("pipx is not resolvable as an executable")
        raise UnsupportedBackendError("pipx could not be found") from None


def pipx_home_dir() -> Path:
    """Where pipx keeps its applications, honouring ``PIPX_HOME``.

    The default is pipx's own documented one - ``~/.local/pipx`` on every
    platform. No subprocess is spawned to ask pipx: the resolution is
    documented and stable, and the application only uses it to say where the
    listed applications live.
    """
    override = os.environ.get("PIPX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "pipx"


class PipxBackend(Backend):
    """A pipx backend: applications in pipx's own home, not an environment."""

    def __init__(self, *, executable: Path | None = None) -> None:
        self._executable = executable

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="pipx",
            can_list=True,
            can_install=True,
            can_uninstall=True,
            can_upgrade=True,
            can_downgrade=False,
            can_upgrade_all=True,
            can_search=False,
            can_show_sizes=False,
            can_install_user=False,
            # pipx's commands write its own home and never the selected
            # environment; the reachability question about what the selected
            # interpreter imports cannot be about it.
            targets_selected_environment=False,
            need_confirmation=True,
        )

    @property
    def executable_name(self) -> str:
        return "pipx"

    def executable_path(self) -> Path:
        if self._executable is not None:
            return self._executable
        try:
            return find_pipx()
        except UnsupportedBackendError as error:
            raise UnsupportedBackendError("pipx is not available on this system") from error

    def policy_for(self, facts: InterpreterFacts) -> ModificationPolicy:
        """pipx writes only under the user's own home; no elevation applies.

        The facts of the *selected environment* are irrelevant to a pipx
        command - it never touches that environment - so the default policy,
        which judges the selected prefix, would sometimes demand the OS's help
        for a command that needs none. pipx's own directories decide, and
        they are the user's.
        """
        return ModificationPolicy(
            allowed=True,
            requires_confirmation=True,
            severity=Severity.OK,
            reason_code="target.pipx_home",
            explanation=(
                "pipx installs applications into your user directory; "
                "the selected environment is not touched."
            ),
            suggestion=None,
        )

    # ------------------------------------------------------------------
    # Changing actions
    # ------------------------------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the pipx argument list for a validated action.

        Downgrade is refused rather than approximated: pipx has no command
        that re-installs one pinned version over another, and a plan is only
        offered when the tool can honour what the confirmation showed. pipx
        has no ``--`` separator; the core validators refuse option-shaped
        names and specs before a plan exists.
        """
        self._require(action)
        if action.user:
            raise UnsupportedActionError(
                "pipx installs applications in its own home; there is no user-site install"
            )

        executable = str(self._executable or self.executable_path())

        if action.kind is ActionKind.UNINSTALL:
            argv = [executable, "uninstall", *action.names]
        elif action.kind is ActionKind.INSTALL:
            argv = [executable, "install", *action.specs]
        elif action.kind is ActionKind.UPGRADE:
            argv = [executable, "upgrade", *action.names]
        else:
            argv = [executable, "upgrade-all"]

        return CommandPlan(
            argv=tuple(argv),
            action=action,
            label=f"pipx {action.kind.value}",
            backend="pipx",
        )

    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """List installed applications using ``pipx list --json``.

        The rows' location is the pipx home - where the applications and
        their venvs live - and not the *environment* argument, which pipx
        pays no attention to.
        """
        executable = str(self._executable or self.executable_path())
        try:
            result = run(
                [executable, "list", "--json"],
                timeout=120.0,
                cancel=cancel,
            )
        except CommandFailedError as error:
            return PackageListing(
                environment=environment,
                backend="pipx",
                packages=(),
                error=error.result.output.strip(),
            )

        if not result.ok:
            return PackageListing(
                environment=environment,
                backend="pipx",
                packages=(),
                error=result.output.strip(),
                timed_out=result.timed_out,
            )

        home = pipx_home_dir()
        packages = tuple(
            InstalledPackage(
                name=name,
                specifier=version,
                location=home,
                backend="pipx",
            )
            for name, version in _parse_pipx_list(result.stdout)
            if name
        )

        return PackageListing(
            environment=home,
            backend="pipx",
            packages=packages,
            cached=False,
        )


def _parse_pipx_list(stdout: str) -> list[tuple[str, str]]:
    """Read pipx's JSON report into ``(application, version)`` pairs.

    The shape is pipx's documented one: ``venvs`` maps each application to
    the venv pipx created, and the venv's ``main_package`` carries the
    version that was installed. Older pipx versions called that field
    ``main_package.package`` - both spellings are read, and an entry that
    carries neither is reported with an empty version rather than dropped:
    an application with an unread version is still installed.
    """
    import json

    try:
        payload = json.loads(stdout)
    except ValueError:
        return []

    venvs = payload.get("venvs") if isinstance(payload, dict) else None
    if not isinstance(venvs, dict):
        return []

    rows: list[tuple[str, str]] = []
    for name, entry in venvs.items():
        if not isinstance(entry, dict):
            continue
        main = entry.get("main_package")
        if not isinstance(main, dict):
            main = {}
        version = main.get("package_version") or main.get("version") or ""
        rows.append((str(name).strip(), str(version).strip()))
    return rows
