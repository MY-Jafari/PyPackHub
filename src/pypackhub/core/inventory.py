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
from builtins import list as _list
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from packaging.version import InvalidVersion, Version

from pypackhub.core.backends.base import (
    ActionKind,
    Backend,
    BackendError,
    CommandPlan,
    InstalledPackage,
    PackageAction,
    PackageListing,
    UnsupportedBackendError,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.uv import UvBackend
from pypackhub.core.metadata import (
    InstalledMetadataIndex,
    own_site_packages_directories,
    scan,
    site_packages_writable,
)
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
    "action_target_names",
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

#: How many package-index lookups run at once. The index has no batch endpoint,
#: so refreshing a 200-package environment is 200 round trips; eight at a time
#: turns that from "minutes" into "a few seconds" without opening a socket per
#: installed package.
LOOKUP_WORKERS: Final = 8


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


def _bounded_map(
    items: Sequence[str],
    work: Callable[[str], PackageIndexInfo],
    *,
    workers: int,
    cancel: threading.Event | None,
    on_result: Callable[[str, PackageIndexInfo], None],
) -> None:
    """Run *work* over *items* with a bounded number of lookups in flight.

    ``on_result`` is called as each answer arrives, from the worker thread that
    produced it, so a caller that only hands the answer to a queue (or a Qt
    signal) sees the table fill in instead of waiting for the last package.

    Cancellation stops *asking*: the loop checks the event before it submits
    more work, rather than finishing every request and throwing the answers away.
    A lookup that raises is reported as an error for that one package - an index
    that refuses one name must not take the rest of the list down with it.
    """
    if not items:
        return

    if workers <= 1:
        for item in items:
            if _is_cancelled(cancel):
                return
            on_result(item, _safe_lookup(work, item))
        return

    with ThreadPoolExecutor(
        max_workers=min(workers, len(items)), thread_name_prefix="pypackhub-index"
    ) as pool:
        queue = iter(items)
        pending: dict[Future[PackageIndexInfo], str] = {}

        while True:
            while len(pending) < workers and not _is_cancelled(cancel):
                try:
                    name = next(queue)
                except StopIteration:
                    break
                pending[pool.submit(work, name)] = name

            if not pending:
                return

            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                name = pending.pop(future)
                on_result(name, _resolve(future, name))


def _safe_lookup(work: Callable[[str], PackageIndexInfo], name: str) -> PackageIndexInfo:
    """Call *work* for one package, turning a failure into an error for that row."""
    # One unreadable or invalid package name must not stop the whole pass: the
    # error belongs to that one row.
    try:
        return work(name)
    except Exception as error:
        return PackageIndexInfo(error=str(error))


def _resolve(future: Future[PackageIndexInfo], name: str) -> PackageIndexInfo:
    """The answer a completed lookup future carries, or the error it raised."""
    try:
        return future.result()
    except Exception as error:
        return PackageIndexInfo(error=f"{name}: {error}")


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
        """List the installed packages. Local only: no network, no size walk.

        The listing merges every usable backend for the environment so a package is
        never invisible just because one manager did not report it. The backend used
        for actions is still decided separately by the user's package-manager choice.
        """
        merged, error, timed_out, backend_name = self._merge_listings()
        return Inventory(
            environment=self._prefix,
            backend=backend_name,
            rows=tuple(merged),
            error=error,
            timed_out=timed_out,
        )

    def _merge_listings(self) -> tuple[_list[PackageRow], str, bool, str]:
        """List through every usable backend and merge the results.

        The backends are asked **at the same time**. Running them one after the
        other made every refresh pay for both - a uv listing and a pip listing,
        each one a process start plus a full read of the environment - while the
        second answer could only ever add packages the first did not report.

        Returns the merged rows (deduplicated by normalised name; a package two
        managers report with different versions counts as the one the
        environment's own interpreter imports - see :meth:`_by_listing_trust`),
        a combined error string, whether any listing timed out, and the name of
        the first backend in preference order that produced rows.
        """
        facts = self.facts
        order = list(self._prefer)

        with ThreadPoolExecutor(
            max_workers=max(1, len(order)), thread_name_prefix="pypackhub-list"
        ) as pool:
            futures = {name: pool.submit(self._list_one, name, facts) for name in order}
            listings = [(name, futures[name].result()) for name in order]

        rows: dict[str, PackageRow] = {}
        error_parts: list[str] = []
        timed_out = False
        backend_name = ""
        usable: _list[PackageListing] = []

        for _, listing in listings:
            if listing is None:
                continue
            if listing.packages and not backend_name:
                backend_name = listing.backend
            if listing.timed_out:
                timed_out = True
            if listing.error and not listing.packages:
                error_parts.append(f"{listing.backend}: {listing.error}")
                continue
            usable.append(listing)

        for listing in self._by_listing_trust(usable):
            for package in listing.packages:
                key = _normalized(package.name)
                if key not in rows:
                    rows[key] = _row_from_package(package)

        error = " | ".join(error_parts) if error_parts else ""
        merged = sorted(rows.values(), key=lambda row: row.name.lower())
        return merged, error, timed_out, backend_name or "pip"

    @staticmethod
    def _by_listing_trust(listings: Sequence[PackageListing]) -> Sequence[PackageListing]:
        """Order finished listings so a conflict lands on the truthful one.

        Two managers can report the same package with different versions in one
        environment, and which copy is real is not a matter of taste. The
        backend that runs the environment's own interpreter - pip, as
        ``<environment python> -m pip list`` - answers in that interpreter's
        ``sys.path`` order, so it reports the copy ``import`` actually finds.
        That distinction is the whole story on an all-users Windows install:
        ``pip install --upgrade pip`` cannot write into ``Program Files`` and
        lands in the user site directory, which shadows the stale system copy -
        while a prefix scan such as uv's never looks at the user site and keeps
        reporting the version from before the upgrade. Believing that scan first
        is how the packages table showed pip 24.3.1 with an upgrade available,
        on a machine where ``python -m pip list`` said 26.2.1.

        The pip listing is therefore inserted first and the others only fill
        the gaps: a package pip did not report (a uv-managed environment with
        no pip of its own, for instance) stays visible exactly as before.
        """
        return sorted(listings, key=lambda listing: listing.backend != "pip")

    def _list_one(self, name: str, facts: InterpreterFacts) -> PackageListing | None:
        """List this environment through one backend, or ``None`` when unusable.

        A backend that is not installed, or that fails outright, is skipped
        rather than raised: the merged listing exists so that one broken or
        missing package manager does not hide the packages the other reports.
        """
        try:
            backend = _make_backend(name, self._prefix, facts.executable)
            backend.executable_path()
        except (UnsupportedBackendError, ValueError, OSError):
            return None

        try:
            return backend.list_packages(self._prefix, facts=facts)
        except BackendError as error:
            return PackageListing(
                environment=self._prefix,
                backend=name,
                packages=(),
                error=str(error),
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
        workers: int = LOOKUP_WORKERS,
    ) -> Inventory:
        """Fill in the latest version for each row, several packages at a time.

        ``on_update`` is called with each updated row as its answer arrives, so
        the table fills in progressively instead of appearing all at once when
        the slowest lookup finishes - and it is called from a worker thread, so a
        caller must hand the row to something thread-safe (a Qt signal, a queue)
        rather than touching a widget. When ``cancel`` is set the pass stops
        asking and returns what it has.
        """
        rows = list(inventory.rows)
        selected = rows if limit is None else rows[:limit]
        if not selected:
            return inventory

        position_of = {row.name: position for position, row in enumerate(selected)}

        def report(name: str, info: PackageIndexInfo) -> None:
            position = position_of[name]
            selected[position] = selected[position].with_index_info(info)
            if on_update is not None:
                on_update(selected[position])

        _bounded_map(
            [row.name for row in selected],
            self.index_info,
            workers=workers,
            cancel=cancel,
            on_result=report,
        )

        rows[: len(selected)] = selected
        return inventory.replace_rows(rows)

    def refresh(
        self,
        *,
        with_sizes: bool = False,
        with_latest: bool = True,
        on_row: Callable[[PackageRow], None] | None = None,
        on_listed: Callable[[Inventory], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Inventory:
        """The whole refresh a user triggers with the Refresh button.

        ``on_listed`` is called with the listing the moment it exists, before any
        network work: what is installed is known locally and the window can show
        it while the latest versions are still being looked up. ``on_row`` is
        called as each package is resolved, so the table fills in instead of
        appearing all at once at the end. Both are called from the calling
        thread, which is a worker thread. When ``with_latest`` is false the
        refresh stays entirely local - which is what a user wants when they know
        they are offline.
        """
        inventory = self.list()
        if on_listed is not None:
            on_listed(inventory)

        # Sizes are local and are measured first: an answer that sits on disk must
        # not queue behind a network round trip, so the table fills in with it
        # while the index is still being asked about the latest versions.
        if with_sizes and inventory.rows:
            inventory = self.enrich_sizes(inventory, on_update=on_row, cancel=cancel)

        if with_latest and inventory.rows:
            inventory = self.enrich_latest(inventory, on_update=on_row, cancel=cancel)

        return inventory

    def enrich_sizes(
        self,
        inventory: Inventory,
        *,
        on_update: Callable[[PackageRow], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Inventory:
        """Measure the on-disk size of each installed package.

        The size of a package is read from that distribution's own ``.dist-info``
        record (see :mod:`pypackhub.core.sizes`), and the metadata index already
        knows where every installed distribution keeps its directory. Measuring
        therefore costs one small file read per package - not a walk over the
        environment repeated once per row, which is what made every package
        report the size of the whole environment.
        """
        from pypackhub.core.sizes import package_sizes

        index = self.metadata_index()
        rows = list(inventory.rows)

        targets: list[tuple[str, Path]] = []
        for row in rows:
            metadata = index.get(row.name)
            if metadata is not None and metadata.location is not None:
                targets.append((row.name, metadata.location))

        measured = package_sizes(targets)

        for position, row in enumerate(rows):
            if _is_cancelled(cancel):
                break
            estimate = measured.get(row.name)
            if estimate is None:
                continue
            rows[position] = row.with_size(estimate.size_bytes)
            if on_update is not None:
                on_update(rows[position])

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

    def plan_with_backend(self, name: str, action: PackageAction) -> CommandPlan:
        """Plan *action* through a different backend for this environment.

        The use case is the reachability refusal: a package whose imported
        copy lives in the user site can only be changed by a backend that
        writes there, and on a base interpreter that backend is pip. The
        returned plan carries the *other* backend's own command - the
        confirmation shows it and the runner runs that same ``argv`` (SPEC
        section 11.7) - and ``prefer=()`` keeps the request literal: asking
        for pip means pip, not "pip, or whatever else is installed".
        """
        backend = build_backend(self._prefix, name=name, prefer=())
        if backend is None:  # pragma: no cover - require=True raises instead
            raise UnsupportedBackendError(f"{name} is not available")
        if not backend.capability.supports(action.kind):
            raise UnsupportedBackendError(f"{name} cannot perform {action.kind.value}")
        return backend.plan(action)

    def execute(
        self,
        plan: CommandPlan,
        *,
        cancel: threading.Event | None = None,
        on_line: Callable[[str], None] | None = None,
        elevated: bool = False,
    ) -> CommandResult:
        """Run a plan through the backend, reporting output as it arrives.

        With ``elevated=True`` the same plan runs through the OS's standard
        elevation mechanism instead: the command is identical, only the
        privileges it is granted differ, and the request covers this one
        action only (SPEC section 11.5). Elevated runs cannot stream output
        on every platform, so ``on_line`` is ignored for them and the caller
        tells the user the log will carry the result rather than the run.
        """
        if elevated:
            return self.backend.execute_elevated(plan, cancel=cancel)
        return self.backend.execute(plan, cancel=cancel, on_line=on_line)

    def backend_needs_elevation(self, action: PackageAction) -> bool:
        """Whether running *action* through this backend needs administrator rights.

        The answer is about the *backend*, not the package: a backend with no
        user-site path of its own - uv has none - can only write the
        environment's own site-packages, and on an all-users install that is
        exactly the directory a normal user may not change. It is the one case
        where pip silently falls back to the user site and uv fails with a
        permission error instead, and it is why ``can_install_user`` is the
        capability this check keys on: a backend that has a user-site path
        always has somewhere writable to go as a normal user, so it never
        needs the OS's help.
        """
        if self.backend.capability.can_install_user:
            return False
        return not site_packages_writable(self._prefix)

    def backend_can_reach_targets(self, action: PackageAction) -> bool:
        """Whether this backend can change what the interpreter imports for *action*.

        On a base interpreter the user site directory shadows the environment's
        own site-packages on ``sys.path``: the copy ``import`` finds - and the
        one this service's metadata index and listings report - is the user's,
        even when a newer one sits next to it in the system directory. A
        backend that writes only the environment's own site-packages (uv has
        no user-site path) therefore *cannot* change the version the user -
        and the packages table - actually see: the command exits 0, the log
        says success, and the row keeps showing the old version. That is not
        a failure to run; it is a run that could never have been visible, and
        it is refused before it happens rather than reported as a success
        that changed nothing.

        A backend that can install into the user site (pip, with its
        ``--user`` fallback) can always reach the shadowing copy, so it never
        trips this check. Packages the index knows nothing about are equally
        safe: nothing shadows them yet, so the install lands where ``import``
        will look.
        """
        if self.backend.capability.can_install_user:
            return True

        index = self.metadata_index()
        own = own_site_packages_directories(self._prefix)

        for name in action_target_names(action):
            metadata = index.get(name)
            if metadata is None or metadata.location is None:
                continue
            if not any(metadata.location.is_relative_to(directory) for directory in own):
                return False

        return True

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

    def action_for_restore(self, specs: Sequence[str]) -> PackageAction:
        """One install plan that pins every recorded version.

        A restore is not "install the latest": the exact pins are the whole
        point, and they arrive as a set because a rollback can restore many
        packages with one command and one confirmation.
        """
        return PackageAction(
            kind=ActionKind.INSTALL,
            environment=self._prefix,
            specs=tuple(specs),
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

    def action_for_uninstall_many(self, names: Iterable[str]) -> PackageAction:
        """One removal plan for a set of packages, e.g. an orphan clean-up.

        A single plan rather than one per package on purpose: the user approves
        one command, sees one command in the confirmation, and the installer
        resolves the set in one pass instead of being run N times.
        """
        selected = tuple(name for name in names if name)
        return PackageAction(
            kind=ActionKind.UNINSTALL,
            environment=self._prefix,
            names=selected,
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


def action_target_names(action: PackageAction) -> set[str]:
    """The package names an action targets, bare, whatever form they arrived in.

    Uninstall and upgrade carry names; install and upgrade-all carry specs such
    as ``selenium>=4``, whose name part is all a per-package question needs.
    A spec that cannot be parsed has already been refused by the action's own
    validation, so dropping it here changes nothing.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    names = {name for name in action.names if name}
    for spec in action.specs:
        try:
            names.add(Requirement(spec).name)
        except InvalidRequirement:  # pragma: no cover - validated earlier
            continue
    return names
