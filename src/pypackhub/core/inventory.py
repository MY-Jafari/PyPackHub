"""The package inventory: everything the packages view needs, without Qt.

This module is the layer the UI asks questions of. It answers "what is installed
in this environment", "which of those are behind PyPI", "how much space do they
take", "what exact command would change this", and "what actually happened" -
and it does so without importing Qt, so the same service can back the CLI
companion and the test-suite.

Three properties matter and are deliberate:

* **Listing is local and fast.** :meth:`InventoryService.list` runs the backend's
  listing command and nothing else. Latest versions come from PyPI over the
  network, so they are a second, progressive pass
  (:meth:`InventoryService.enrich_latest`) that reports each answer as it arrives
  and can be cancelled. A slow or absent network never delays the table.
* **The expensive passes are opt-in and cancellable.** Sizes walk
  ``site-packages``; the metadata index reads every ``dist-info``. Both are
  separate calls with progress, driven by the caller.
* **Planning and executing are separate calls.** :meth:`plan` builds the
  :class:`~pypackhub.core.backends.base.CommandPlan` the confirmation dialog
  shows; :meth:`execute` runs exactly that plan. The command a user approves is
  derived from the same argument list that runs (SPEC section 11.7).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from packaging.version import InvalidVersion, Version

from pypackhub.core.backends.base import (
    ActionKind,
    Backend,
    CommandPlan,
    InstalledPackage,
    PackageAction,
    PackageListing,
    UnsupportedBackendError,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.uv import UvBackend
from pypackhub.core.metadata import InstalledMetadataIndex, scan
from pypackhub.core.platform_guard import (
    InterpreterFacts,
    ModificationPolicy,
    current_prefix,
    inspect,
    modification_policy,
)
from pypackhub.core.runner import CommandResult

__all__ = [
    "Inventory",
    "InventoryService",
    "PackageIndexInfo",
    "PackageRow",
    "PackageStatus",
    "build_backend",
    "compare_versions",
]


class PackageStatus(str, Enum):
    """How an installed package compares with what is published."""

    UP_TO_DATE = "up_to_date"
    OUTDATED = "outdated"
    UNKNOWN = "unknown"


def compare_versions(installed: str, latest: str) -> PackageStatus:
    """Compare two version strings without ever raising.

    Metadata from PyPI and from a local installation is untrusted input: a
    version that cannot be parsed means "unknown", never a crash and never a
    guess about which is newer.
    """
    if not installed or not latest:
        return PackageStatus.UNKNOWN

    try:
        installed_version = Version(installed)
        latest_version = Version(latest)
    except InvalidVersion:
        return PackageStatus.UNKNOWN

    if installed_version >= latest_version:
        return PackageStatus.UP_TO_DATE
    return PackageStatus.OUTDATED


class PackageIndex(Protocol):
    """The part of the PyPI client this service uses, so tests can fake it."""

    def info(self, package: str) -> object:
        """Return an object with ``latest_version``, ``summary`` and ``error``."""


@dataclass(frozen=True, slots=True)
class PackageIndexInfo:
    """What the package index knows about one package."""

    latest: str = ""
    summary: str = ""
    license: str = ""
    home_page: str = ""
    project_url: str = ""
    requires_python: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class PackageRow:
    """One row of the packages table."""

    name: str
    installed: str
    latest: str = ""
    status: PackageStatus = PackageStatus.UNKNOWN
    size_bytes: int | None = None
    summary: str = ""
    license: str = ""
    home_page: str = ""
    project_url: str = ""
    requires_python: str = ""
    location: Path | None = None
    backend: str = ""
    editable: bool = False
    direct_reference: bool = False
    error: str = ""

    def with_index_info(self, info: PackageIndexInfo) -> PackageRow:
        """Return this row updated with what the package index reported."""
        if info.error:
            return replace(self, error=info.error)

        return replace(
            self,
            latest=info.latest,
            summary=info.summary or self.summary,
            license=info.license or self.license,
            home_page=info.home_page or self.home_page,
            project_url=info.project_url or self.project_url,
            requires_python=info.requires_python or self.requires_python,
            status=compare_versions(self.installed, info.latest),
        )

    def with_size(self, size_bytes: int | None) -> PackageRow:
        return replace(self, size_bytes=size_bytes)

    @property
    def sort_size_bytes(self) -> int:
        """Size for sorting, with "not measured" treated as zero."""
        return self.size_bytes or 0

    @property
    def is_outdated(self) -> bool:
        return self.status is PackageStatus.OUTDATED


@dataclass(frozen=True, slots=True)
class Inventory:
    """A snapshot of one environment's installed packages."""

    environment: Path
    backend: str
    rows: tuple[PackageRow, ...] = ()
    error: str = ""
    timed_out: bool = False

    @property
    def outdated_count(self) -> int:
        return sum(1 for row in self.rows if row.is_outdated)

    @property
    def total_size_bytes(self) -> int:
        return sum(row.size_bytes or 0 for row in self.rows)

    @property
    def measured_size_count(self) -> int:
        return sum(1 for row in self.rows if row.size_bytes is not None)

    @property
    def unknown_count(self) -> int:
        return sum(1 for row in self.rows if row.status is PackageStatus.UNKNOWN)

    def row(self, name: str) -> PackageRow | None:
        canonical = _normalized(name)
        for row in self.rows:
            if _normalized(row.name) == canonical:
                return row
        return None

    def replace_rows(self, rows: Iterable[PackageRow]) -> Inventory:
        return replace(self, rows=tuple(rows))


def _normalized(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _row_from_package(package: InstalledPackage) -> PackageRow:
    return PackageRow(
        name=package.name,
        installed=package.specifier,
        location=package.location,
        backend=package.backend,
        editable=package.editable,
        direct_reference=package.direct_reference,
    )


#: Preference order when picking a backend for an environment. uv is first
#: because SPEC section 2 asks for it to be preferred when it is present.
PREFERRED_BACKENDS: Final = ("uv", "pip")


def _make_backend(name: str, prefix: Path, interpreter: Path | None) -> Backend:
    if name == "uv":
        return UvBackend(python=interpreter)
    if name == "pip":
        return PipBackend(prefix=prefix, python=interpreter)
    raise ValueError(f"unknown backend {name!r}")


def build_backend(
    prefix: Path,
    *,
    name: str = "",
    prefer: Sequence[str] = PREFERRED_BACKENDS,
    require: bool = True,
) -> Backend | None:
    """Return the backend to use for *prefix*, or ``None`` when none is available.

    ``name`` requests a specific backend; it still has to be available on this
    machine, and the preference order is then tried as a fallback. Availability
    is decided by resolving the executable, so a backend that is not installed
    never claims a job it cannot run. With ``require=False`` an unavailable
    backend is reported as ``None`` so the UI can say "no package manager found"
    instead of failing later.
    """
    interpreter = inspect(prefix).executable
    order = ([name] if name else []) + [candidate for candidate in prefer if candidate != name]

    for candidate in order:
        try:
            backend = _make_backend(candidate, prefix, interpreter)
            backend.executable_path()
        except (UnsupportedBackendError, ValueError, OSError):
            continue
        return backend

    if require:
        raise UnsupportedBackendError(
            "no usable package manager was found (tried: " + ", ".join(order) + ")"
        )
    return None


class InventoryService:
    """The packages view's core: listing, enrichment, planning and execution."""

    def __init__(
        self,
        *,
        prefix: Path | None = None,
        backend: Backend | None = None,
        backend_name: str = "",
        pypi_client: PackageIndex | None = None,
        prefer_uv: bool = True,
    ) -> None:
        self._prefix = Path(prefix).resolve() if prefix is not None else current_prefix()
        self._prefer: tuple[str, ...] = ("uv", "pip") if prefer_uv else ("pip",)
        self._backend = backend
        self._backend_name = backend_name
        self._pypi = pypi_client
        self._facts: InterpreterFacts | None = None
        self._metadata: InstalledMetadataIndex | None = None

    # -- environment ----------------------------------------------------

    @property
    def environment(self) -> Path:
        return self._prefix

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            built = build_backend(self._prefix, name=self._backend_name, prefer=self._prefer)
            if built is None:  # pragma: no cover - require=True raises instead
                raise UnsupportedBackendError(
                    "no usable package manager was found in " + str(self._prefix)
                )
            self._backend = built
        return self._backend

    @property
    def backend_name(self) -> str:
        return self.backend.capability.name

    @property
    def facts(self) -> InterpreterFacts:
        if self._facts is None:
            self._facts = inspect(self._prefix)
        return self._facts

    def policy(self) -> ModificationPolicy:
        """Whether this environment may be modified, and how loudly to warn."""
        return modification_policy(self.facts)

    # -- listing --------------------------------------------------------

    def list(self) -> Inventory:
        """List the installed packages. Local only: no network, no size walk."""
        listing: PackageListing = self.backend.list_packages(self._prefix, facts=self.facts)

        if listing.error and not listing.packages:
            return Inventory(
                environment=self._prefix,
                backend=listing.backend,
                error=listing.error,
                timed_out=listing.timed_out,
            )

        rows = sorted(
            (_row_from_package(package) for package in listing.packages),
            key=lambda row: row.name.lower(),
        )
        return Inventory(
            environment=self._prefix,
            backend=listing.backend,
            rows=tuple(rows),
            error=listing.error,
            timed_out=listing.timed_out,
        )

    def index_info(self, name: str) -> PackageIndexInfo:
        """Ask the package index for one package.

        A network failure is reported as an error on the row rather than raised:
        an offline machine must still show the installed packages.
        """
        client = self._pypi
        if client is None:
            from pypackhub.core.pypi import PyPIClient

            client = PyPIClient()
            self._pypi = client

        info = client.info(name)
        return PackageIndexInfo(
            latest=str(getattr(info, "latest_version", "") or ""),
            summary=str(getattr(info, "summary", "") or ""),
            home_page=str(getattr(info, "home_page", "") or ""),
            project_url=str(getattr(info, "project_url", "") or ""),
            requires_python=str(getattr(info, "requires_python", "") or ""),
            error=str(getattr(info, "error", "") or ""),
        )

    def enrich_latest(
        self,
        inventory: Inventory,
        *,
        on_update: Callable[[PackageRow], None] | None = None,
        cancel: threading.Event | None = None,
        limit: int | None = None,
    ) -> Inventory:
        """Fill in the latest version for each row, one package at a time.

        ``on_update`` is called with each updated row so the table can fill in
        progressively. When ``cancel`` is set the pass stops and returns what it
        has.
        """
        rows = list(inventory.rows)
        selected = rows if limit is None else rows[:limit]

        for index, row in enumerate(selected):
            if _is_cancelled(cancel):
                break

            rows[index] = row.with_index_info(self.index_info(row.name))
            if on_update is not None:
                on_update(rows[index])

        return inventory.replace_rows(rows)

    def refresh(
        self,
        *,
        with_sizes: bool = False,
        with_latest: bool = True,
        on_row: Callable[[PackageRow], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Inventory:
        """The whole refresh a user triggers with the Refresh button.

        ``on_row`` is called as each package is resolved, so the table fills in
        instead of appearing all at once at the end. When ``with_latest`` is
        false the refresh stays entirely local - which is what a user wants when
        they know they are offline.
        """
        inventory = self.list()

        if with_latest and inventory.rows:
            inventory = self.enrich_latest(inventory, on_update=on_row, cancel=cancel)

        if with_sizes and inventory.rows:
            inventory = self.enrich_sizes(inventory, on_update=on_row, cancel=cancel)

        return inventory

    def enrich_sizes(
        self,
        inventory: Inventory,
        *,
        on_update: Callable[[PackageRow], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Inventory:
        """Measure the on-disk size of each installed package."""
        from pypackhub.core.sizes import package_sizes

        rows = list(inventory.rows)
        targets = [(row.name, row.location) for row in rows if row.location is not None]

        measured = package_sizes(targets)

        for index, row in enumerate(rows):
            if _is_cancelled(cancel):
                break
            estimate = measured.get(row.name)
            if estimate is None:
                continue
            rows[index] = row.with_size(estimate.size_bytes)
            if on_update is not None:
                on_update(rows[index])

        return inventory.replace_rows(rows)

    def metadata_index(self, *, refresh: bool = False) -> InstalledMetadataIndex:
        """The installed distribution metadata, read once and cached.

        This is the expensive pass behind the detail panel, so it is cached per
        service instance and invalidated explicitly after a change.
        """
        if self._metadata is None or refresh:
            self._metadata = scan(self._prefix)
        return self._metadata

    def invalidate_metadata(self) -> None:
        """Forget the cached metadata, after something in the environment changed."""
        self._metadata = None

    # -- changing actions ----------------------------------------------

    def plan(self, action: PackageAction) -> CommandPlan:
        """Build the command that would run *action*, validated and unexecuted."""
        if not self.backend.capability.supports(action.kind):
            raise UnsupportedBackendError(f"{self.backend_name} cannot perform {action.kind.value}")
        return self.backend.plan(action)

    def execute(
        self,
        plan: CommandPlan,
        *,
        cancel: threading.Event | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Run a plan through the backend, reporting output as it arrives."""
        return self.backend.execute(plan, cancel=cancel, on_line=on_line)

    def outdated_names(self, inventory: Inventory) -> tuple[str, ...]:
        """The names of the outdated packages, in table order."""
        return tuple(row.name for row in inventory.rows if row.is_outdated)

    # -- action builders ------------------------------------------------

    def action_for_install(self, spec: str, *, user: bool = False) -> PackageAction:
        return PackageAction(
            kind=ActionKind.INSTALL,
            environment=self._prefix,
            specs=(spec,),
            user=user,
        )

    def action_for_uninstall(self, name: str) -> PackageAction:
        return PackageAction(kind=ActionKind.UNINSTALL, environment=self._prefix, names=(name,))

    def action_for_upgrade(self, name: str, *, user: bool = False) -> PackageAction:
        return PackageAction(
            kind=ActionKind.UPGRADE,
            environment=self._prefix,
            names=(name,),
            user=user,
        )

    def action_for_downgrade(self, name: str, version: str, *, user: bool = False) -> PackageAction:
        return PackageAction(
            kind=ActionKind.DOWNGRADE,
            environment=self._prefix,
            names=(name,),
            version=version,
            user=user,
        )

    def action_for_upgrade_all(self, inventory: Inventory, *, user: bool = False) -> PackageAction:
        """One plan that upgrades every outdated package in the inventory."""
        specs = self.outdated_names(inventory)
        return PackageAction(
            kind=ActionKind.UPGRADE_ALL,
            environment=self._prefix,
            specs=specs,
            user=user,
        )


def _is_cancelled(cancel: threading.Event | None) -> bool:
    """True when the cancellation event (if any) has been set."""
    return cancel is not None and cancel.is_set()
