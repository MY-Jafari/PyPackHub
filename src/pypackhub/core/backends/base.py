"""Shared backend interface for package-manager backends.

Every backend in this project talks to exactly one package manager through the
hardened runner in :mod:`pypackhub.core.runner`. This module is the contract:
what the rest of the application may ask of a backend without knowing whether it
is pip, uv, conda, poetry, pipx, pipenv or pdm.

Changing an environment is deliberately split into two steps, because SPEC
section 11.7 requires the command a user confirms to be *the same data that is
executed*:

1. :meth:`Backend.plan` turns a validated request into a :class:`CommandPlan` -
   an argument list plus the label shown in the confirmation dialog. It spawns
   nothing. The plan's ``display_command()`` is derived from the same ``argv``
   that will be run, so the dialog and the execution cannot drift apart.
2. :meth:`Backend.execute` runs a plan through the runner, with cancellation and
   a line callback for the live log. It is implemented once, here, so every
   backend inherits the same guarantees and there is exactly one execution path
   to audit.

Everything that reaches ``argv`` is validated first: package specs, package
names and versions are checked in :class:`PackageAction` before a plan exists, so
a hostile name cannot become an option.
"""

from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from packaging.version import InvalidVersion, Version

from pypackhub.core.platform_guard import InterpreterFacts, ModificationPolicy
from pypackhub.core.runner import (
    DEFAULT_TIMEOUT,
    CommandResult,
    format_argument,
    run,
    run_elevated,
)
from pypackhub.core.secrets import redact
from pypackhub.core.validation import (
    Reason,
    ValidationError,
    validate_package_name,
    validate_package_spec,
)

__all__ = [
    "ActionKind",
    "Backend",
    "BackendCapability",
    "BackendError",
    "CommandPlan",
    "InstalledPackage",
    "PackageAction",
    "PackageListing",
    "UnsupportedActionError",
    "UnsupportedBackendError",
    "iter_display_lines",
    "script_path_in_prefix",
]


class BackendError(RuntimeError):
    """A backend command failed or cannot be run on this machine."""


class UnsupportedBackendError(BackendError):
    """The requested backend is not available on this machine."""


class UnsupportedActionError(BackendError):
    """This backend cannot perform the requested action."""


class ActionKind(str, Enum):
    """The changing actions the packages view can ask for."""

    INSTALL = "install"
    UNINSTALL = "uninstall"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    UPGRADE_ALL = "upgrade_all"

    @property
    def is_destructive(self) -> bool:
        """True when the action removes something that is installed."""
        return self is ActionKind.UNINSTALL


@dataclass(frozen=True, slots=True)
class BackendCapability:
    """What a backend can do today, without spawning anything to find out."""

    name: str
    can_list: bool = True
    can_install: bool = False
    can_uninstall: bool = False
    can_upgrade: bool = False
    can_downgrade: bool = False
    can_upgrade_all: bool = False
    can_search: bool = False
    can_show_sizes: bool = False
    #: Whether the backend can install into the user site directory.
    can_install_user: bool = False
    #: Whether this backend's commands write the selected environment at all.
    #: pipx installs applications in its own home and never touches the
    #: selected prefix, so questions about the selected environment - the
    #: reachability check, the size column - are either vacuous or answered
    #: for its own scope, never for the prefix.
    targets_selected_environment: bool = True
    need_confirmation: bool = True

    def supports(self, action: ActionKind) -> bool:
        """True when this backend advertises *action*."""
        mapping = {
            ActionKind.INSTALL: self.can_install,
            ActionKind.UNINSTALL: self.can_uninstall,
            ActionKind.UPGRADE: self.can_upgrade,
            ActionKind.DOWNGRADE: self.can_downgrade,
            ActionKind.UPGRADE_ALL: self.can_upgrade_all,
        }
        return mapping[action]


@dataclass(frozen=True, slots=True)
class InstalledPackage:
    """One package reported by a backend."""

    name: str
    specifier: str
    location: Path
    editable: bool = False
    direct_reference: bool = False
    backend: str = ""


@dataclass(frozen=True, slots=True)
class PackageListing:
    """The result of listing an environment through one backend."""

    environment: Path
    backend: str
    packages: tuple[InstalledPackage, ...]
    error: str = ""
    timed_out: bool = False
    cached: bool = False


@dataclass(frozen=True, slots=True)
class PackageAction:
    """A validated request to change one environment.

    This is the *only* way to describe a change, and it validates itself on
    construction: by the time a :class:`CommandPlan` exists, every value that
    reaches the command line has passed the core validators. A caller that wants
    to reject bad input early can catch :class:`~pypackhub.core.validation.ValidationError`
    here rather than after a process has been spawned.
    """

    kind: ActionKind
    environment: Path
    #: Validated package specs, e.g. ``flask>=3,<4``. Used by install and
    #: upgrade-all.
    specs: tuple[str, ...] = ()
    #: Validated bare package names. Used by uninstall, upgrade and downgrade.
    names: tuple[str, ...] = ()
    #: Validated target version. Only downgrade uses it.
    version: str = ""
    #: Install into the user site directory instead of the environment.
    user: bool = False

    def __post_init__(self) -> None:
        cleaned_specs = tuple(validate_package_spec(spec) for spec in self.specs)
        cleaned_names = tuple(validate_package_name(name) for name in self.names)

        object.__setattr__(self, "specs", cleaned_specs)
        object.__setattr__(self, "names", cleaned_names)

        if self.version:
            object.__setattr__(self, "version", _validated_version(self.version))

        needs_specs = {ActionKind.INSTALL, ActionKind.UPGRADE_ALL}
        needs_names = {ActionKind.UNINSTALL, ActionKind.UPGRADE}
        needs_name_and_version = {ActionKind.DOWNGRADE}

        if self.kind in needs_specs and not cleaned_specs:
            raise ValidationError(
                Reason.EMPTY,
                "this action needs at least one package spec",
                value=repr(self.kind),
            )
        if self.kind in needs_names and not cleaned_names:
            raise ValidationError(
                Reason.EMPTY,
                "this action needs at least one package name",
                value=repr(self.kind),
            )
        if self.kind in needs_name_and_version and not (cleaned_names and self.version):
            raise ValidationError(
                Reason.EMPTY,
                "downgrade needs one package name and one version",
                value=repr(self.kind),
            )
        if self.user and self.kind is ActionKind.UNINSTALL:
            # pip uninstall has no --user: the package is identified by name and
            # the target is whatever environment is being used.
            raise ValidationError(
                Reason.UNSUPPORTED,
                "--user does not apply to an uninstall",
                value="--user",
            )

    def display_target(self) -> str:
        """A short human description of what the action touches."""
        return ", ".join((*self.specs, *self.names)) or self.version


@dataclass(frozen=True, slots=True)
class CommandPlan:
    """An argument list that is ready to run, plus how to describe it.

    ``argv`` is the exact sequence handed to the runner. ``display_command()``
    is derived from it, so the string shown in a confirmation dialog is the
    command that runs - not a re-rendering of the request that could differ.

    ``cwd`` is part of the same promise for the project tools: poetry,
    pipenv and pdm find *their* project by looking at the current directory,
    so a plan that runs them is only complete when it also says where. It is
    ``None`` for every backend that targets an explicit environment instead
    (pip's ``--python``, conda's ``--prefix``), and the base execution path
    hands it to the runner unchanged.
    """

    argv: tuple[str, ...]
    action: PackageAction
    label: str
    backend: str = ""
    cwd: Path | None = None

    def display_command(self) -> str:
        """The command as it will be shown to the user, with secrets redacted."""
        return redact(" ".join(format_argument(argument) for argument in self.argv))

    def matches(self, other: CommandPlan) -> bool:
        """True when two plans would execute the identical command line."""
        return self.argv == other.argv


def _validated_version(version: str) -> str:
    """Validate a bare version string, so it can be pinned as ``name==version``."""
    stripped = version.strip()
    try:
        Version(stripped)
    except InvalidVersion as error:
        raise ValidationError(
            Reason.INVALID_VERSION,
            "not a valid version",
            value=stripped,
        ) from error
    return stripped


#: Actions that install something, and therefore are the ones that respect the
#: ``--user`` flag.
_INSTALLING_ACTIONS: Final = frozenset(
    {ActionKind.INSTALL, ActionKind.UPGRADE, ActionKind.DOWNGRADE, ActionKind.UPGRADE_ALL}
)


class Backend(ABC):
    """One package manager PyPackHub can talk to."""

    @property
    @abstractmethod
    def capability(self) -> BackendCapability: ...

    @property
    @abstractmethod
    def executable_name(self) -> str:
        """The bare command name this backend looks for on the search path."""

    @abstractmethod
    def executable_path(self) -> Path:
        """The resolved executable this backend would actually run, or raises."""

    @abstractmethod
    def list_packages(
        self,
        environment: Path,
        *,
        facts: InterpreterFacts | None = None,
        cancel: threading.Event | None = None,
    ) -> PackageListing:
        """Return the installed packages in *environment* through this backend."""

    @abstractmethod
    def plan(self, action: PackageAction) -> CommandPlan:
        """Turn a validated action into the argument list that would run it.

        Implementations must refuse an action the backend cannot perform, and
        must not spawn anything.
        """

    def supports_environment(self, path: Path) -> bool:
        """Whether this backend can act on the location at *path*.

        The default says yes because a backend that targets an explicit
        environment - pip's ``--python``, uv's ``--python``, conda's
        ``--prefix`` - can be pointed anywhere. The backends whose manager
        instead *finds* its target by location override this: poetry, pipenv
        and pdm drive a project folder and would misfire on an environment,
        conda drives conda environments and would corrupt a venv, and pip
        without an interpreter would run a ``pip`` from the search path that
        belongs to some other prefix. The factory asks before it hands out a
        backend, so a manager forced in the settings never runs against a
        location it would misread.
        """
        return True

    # ------------------------------------------------------------------
    # Shared execution
    # ------------------------------------------------------------------

    def supports(self, action: PackageAction) -> bool:
        """Whether this backend advertises the action, plus the ``--user`` flag."""
        if self.capability.supports(action.kind):
            return not (action.user and not self.capability.can_install_user)
        return False

    def execute(
        self,
        plan: CommandPlan,
        *,
        cancel: threading.Event | None = None,
        on_line: Callable[[str], None] | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        """Run a plan through the hardened runner.

        This is the single execution path in the project: backends do not call
        the runner themselves, so a plan cannot be run with different options
        from the ones it was built with. ``on_line`` receives each line of stdout
        and stderr as it arrives, for the live log. The plan's ``cwd`` travels
        with it, so a project tool runs inside the project the confirmation
        described - never in whatever directory the application inherited.
        """
        return run(
            plan.argv,
            timeout=timeout,
            cancel=cancel,
            cwd=plan.cwd,
            on_stdout=on_line,
            on_stderr=on_line,
        )

    def execute_elevated(
        self,
        plan: CommandPlan,
        *,
        cancel: threading.Event | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a confirmed plan through the OS's standard elevation mechanism.

        The plan's own argument list runs and nothing else: the command is
        the one the user approved, only the privileges it is granted differ
        (SPEC section 11.5 - one action, explained beforehand, never
        persisted). ``on_line`` receives the run's real output as it
        arrives - on Windows through the log file the elevated helper
        writes and the runner follows - so a run that needs the OS's
        consent is still a run the user can watch happening.
        """
        return run_elevated(plan.argv, timeout=timeout, cancel=cancel, on_line=on_line)

    def policy_for(self, facts: InterpreterFacts) -> ModificationPolicy:
        """Default policy delegate: most backends only need the platform guard."""
        from pypackhub.core.platform_guard import modification_policy

        return modification_policy(facts)

    def needs_elevation(self, action: PackageAction, prefix: Path) -> bool:
        """Whether running *action* through this backend needs the OS's help.

        The default answers for a backend that writes the environment at
        *prefix*. Two capabilities short-circuit it: a backend with a
        user-site path of its own always has somewhere writable to go as a
        normal user (pip), and a backend whose commands never touch the
        selected environment is judged by its own scope, never by the
        prefix's permissions (pipx - elevating its commands would lose their
        output to grant privileges they do not need). What remains is the
        environment's own site-packages, and only a probe of it answers.

        The project tools override this: their commands write the project
        folder the action names, not the environment's site-packages, so that
        is the directory probed.
        """
        del action
        if self.capability.can_install_user:
            return False
        if not self.capability.targets_selected_environment:
            return False

        from pypackhub.core.metadata import site_packages_writable

        return not site_packages_writable(prefix)

    def validate_package_spec(self, spec: str) -> str:
        """Reuse the core validator unless a backend needs stricter rules."""
        return validate_package_spec(spec)

    # ------------------------------------------------------------------
    # Helpers for implementations
    # ------------------------------------------------------------------

    def _with_user(self, action: PackageAction, arguments: list[str]) -> list[str]:
        """Add ``--user`` where it means something and the backend supports it."""
        if not action.user:
            return arguments
        if action.kind not in _INSTALLING_ACTIONS:
            return arguments
        if not self.capability.can_install_user:
            raise UnsupportedActionError(
                f"{self.capability.name} cannot install into the user site directory"
            )
        return [*arguments, "--user"]

    def _pinned_specs(self, action: PackageAction) -> tuple[str, ...]:
        """The specs an install-style action should install.

        Downgrade is an install of an exact pin, built here from a validated name
        and a validated version rather than from a string the UI assembled.
        """
        if action.kind is ActionKind.DOWNGRADE:
            return tuple(f"{name}=={action.version}" for name in action.names)
        return action.specs

    def download_plan(self, action: PackageAction) -> CommandPlan | None:
        """A plan that downloads the action's targets (and their dependencies)
        without installing them, so the subsequent install can run from a cache.

        Return ``None`` when the backend has nothing special to do (for example
        when the install itself already resolves and downloads in one pass). This is
        used for upgrade paths where a package manager cannot reliably upgrade
        itself while it is running: download first, then install from the cache.
        """
        return None

    def _require(self, action: PackageAction) -> None:
        """Refuse an action this backend cannot perform."""
        if not self.capability.supports(action.kind):
            raise UnsupportedActionError(
                f"{self.capability.name} cannot perform {action.kind.value}"
            )


def script_path_in_prefix(prefix: Path, name: str) -> Path:
    """Where a pip-installed command's script lands in *prefix*, before it exists.

    The bootstrap flow plans the chosen manager's command before that manager
    is installed: ``python -m pip install poetry`` puts a ``poetry`` script in
    the environment's script directory, and this is that path, so the plan's
    first word is a real location rather than a bare name the hardened runner
    would refuse to resolve.
    """
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return prefix / scripts / f"{name}{suffix}"


def iter_display_lines(result: CommandResult) -> Iterable[str]:
    """Yield the lines a log panel should show for a finished command.

    Kept here next to the plan/execute pair so every caller renders a result the
    same way: the command that ran, then its output.
    """
    yield f"$ {result.display_command()}"
    for line in result.output.splitlines():
        if line:
            yield line
