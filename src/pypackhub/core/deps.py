"""The dependency graph of one environment, and the clean-up built on top.

Everything here answers a question a user asks *before* changing anything:

* what does this package need, directly and transitively?
* what needs this package - and if I remove it, what breaks?
* why is this package here at all?
* which packages does nothing need?

The graph is built from :mod:`pypackhub.core.metadata` - the installed
distributions' own ``METADATA`` files and the installers' ``REQUESTED`` markers -
and never from a network call or a guess. That is what makes an answer here
evidence rather than a heuristic:

* an **edge** exists only when an installed distribution declares the requirement
  *and* the requirement is installed, because an edge to something that is not
  there is not a relationship between two installed packages;
* a requirement that is declared but *not* installed is reported separately as
  missing, because that is a broken environment and the user needs to see it;
* a package is only an **orphan** when nothing installed requires it *and* the
  installer did not record that the user asked for it. The second half matters:
  without it, every package the user installed on purpose but that nothing else
  uses would be called rubbish.

Nothing in this module spawns a process or touches the network. It is pure graph
arithmetic over data the caller already read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from packaging.requirements import InvalidRequirement, Requirement

from pypackhub.core.metadata import InstalledMetadataIndex, normalize_name

__all__ = [
    "DependencyGraph",
    "DependencyNode",
    "OrphanReport",
    "RemovalPreview",
    "build_graph",
]

#: How deep a traversal goes before it stops expanding. A real dependency tree is
#: shallow; the cap is there so a pathological or hostile metadata set cannot turn
#: a click into an unbounded walk.
MAX_DEPTH: int = 32

#: Which way an edge is walked. ``OUT`` follows what a package needs, ``IN``
#: follows what needs it.
Direction = Literal["out", "in"]


@dataclass(frozen=True, slots=True)
class DependencyNode:
    """One installed distribution in the graph."""

    #: The name exactly as the distribution declares it, for display.
    name: str
    #: The PEP 503 normalised name, for identity.
    key: str
    version: str
    #: True when the installer recorded that the user asked for this package.
    requested: bool
    #: Normalised keys of the installed distributions this one requires.
    requires: tuple[str, ...] = ()
    #: Normalised keys of the installed distributions that require this one.
    required_by: tuple[str, ...] = ()
    #: Requirements this distribution declares that are not installed here.
    missing: tuple[str, ...] = ()
    #: True when the distribution's own metadata could not be read.
    unknown: bool = False

    @property
    def is_orphan_candidate(self) -> bool:
        """Nothing requires this, and the user never asked for it by name."""
        return not self.required_by and not self.requested


@dataclass(frozen=True, slots=True)
class RemovalPreview:
    """What removing a set of packages would actually do, before it is done.

    This is the dry run: it is computed from the graph, so it is exact about the
    relationships it can see and free of the side effects a real removal would
    have. The parts that are *not* the request are the point:

    * ``blocked`` - something that is staying still requires one of these, so the
      removal is not the simple request it appears to be;
    * ``breaks`` - a dependent would be left with a requirement that is no longer
      installed;
    * ``would_orphan`` - the removal makes something else unnecessary, but that
      something is a package the user asked for, so it is reported and left alone.
    """

    #: The removal that was asked about, normalised, in the order given.
    requested: tuple[str, ...] = ()
    #: Packages that would be removed, as display names.
    would_remove: tuple[str, ...] = ()
    #: Packages outside the request that would be left with nothing needing
    #: them. This is the cascade the preview exists for: removing ``requests``
    #: leaves ``certifi`` and ``urllib3`` installed but unused.
    would_leave_unused: tuple[str, ...] = ()
    #: Packages in the request that something staying behind still requires.
    blocked: tuple[str, ...] = ()
    #: Dependents left with a missing requirement by this removal.
    breaks: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.would_remove

    @property
    def is_safe(self) -> bool:
        """True when nothing outside the removal set would be broken."""
        return not self.blocked


@dataclass(frozen=True, slots=True)
class OrphanReport:
    """The packages nothing needs, and the ones that are only awkward."""

    #: Nothing requires these and the user never asked for them by name.
    orphans: tuple[str, ...] = ()
    #: Nothing requires these, but the user did ask for them. Reported so the
    #: count is not a surprise, never offered for removal by default.
    requested_only: tuple[str, ...] = ()
    #: Total measured size of the orphans in bytes.
    orphan_size_bytes: int = 0
    #: How many orphans had a measurement, so a total is never read as complete.
    measured: int = 0

    @property
    def count(self) -> int:
        return len(self.orphans)


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """The installed distributions of one environment and the edges between them."""

    environment: Path | None = None
    nodes: dict[str, DependencyNode] = field(default_factory=dict)

    # -- access ---------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and normalize_name(name) in self.nodes

    def get(self, name: str) -> DependencyNode | None:
        return self.nodes.get(normalize_name(name))

    def name_of(self, key: str) -> str:
        """The display name for a normalised key, falling back to the key."""
        node = self.nodes.get(key)
        return node.name if node is not None else key

    @property
    def requested(self) -> tuple[str, ...]:
        """Display names of the packages the user asked for by name."""
        return tuple(node.name for node in self.nodes.values() if node.requested)

    # -- direct relationships --------------------------------------------------

    def requires(self, name: str) -> tuple[str, ...]:
        """Display names of the installed distributions *name* requires."""
        node = self.get(name)
        if node is None:
            return ()
        return tuple(self.name_of(key) for key in node.requires)

    def required_by(self, name: str) -> tuple[str, ...]:
        """Display names of the installed distributions that require *name*."""
        node = self.get(name)
        if node is None:
            return ()
        return tuple(self.name_of(key) for key in node.required_by)

    def missing(self, name: str) -> tuple[str, ...]:
        """Requirements *name* declares that are not installed here."""
        node = self.get(name)
        return node.missing if node is not None else ()

    def is_direct_requirement(self, name: str) -> bool:
        """True when the user asked for this package by name."""
        node = self.get(name)
        return bool(node and node.requested)

    def neighbours(self, key: str, direction: Direction) -> tuple[str, ...]:
        """The keys one edge away from *key*, in the given direction."""
        node = self.nodes.get(key)
        if node is None:
            return ()
        return node.requires if direction == "out" else node.required_by

    # -- traversals ------------------------------------------------------------

    def subgraph(
        self,
        name: str,
        *,
        direction: Direction = "out",
        depth: int = MAX_DEPTH,
    ) -> dict[str, tuple[str, ...]]:
        """The reachable part of the graph from *name*, as ``{source: children}``.

        Breadth-first and cycle-safe, so a package that (invalidly) depends on
        itself, or a genuine loop between two packages, costs a visit rather than
        a hang. Only edges between reached nodes are returned, so a caller can
        render exactly the lines it may draw.
        """
        root = normalize_name(name)
        if root not in self.nodes:
            return {}

        limit = max(1, min(depth, MAX_DEPTH))
        subgraph: dict[str, tuple[str, ...]] = {}
        frontier = [root]

        for _ in range(limit):
            next_frontier: list[str] = []
            for key in frontier:
                children = tuple(
                    child for child in self.neighbours(key, direction) if child in self.nodes
                )
                subgraph[key] = children
                for child in children:
                    if child not in subgraph and child not in next_frontier:
                        next_frontier.append(child)
            if not next_frontier:
                break
            frontier = next_frontier

        return subgraph

    def requires_tree(self, name: str, *, depth: int = MAX_DEPTH) -> dict[str, tuple[str, ...]]:
        """Every package *name* needs, transitively, as ``{source: children}``."""
        return self.subgraph(name, direction="out", depth=depth)

    def required_by_tree(self, name: str, *, depth: int = MAX_DEPTH) -> dict[str, tuple[str, ...]]:
        """Everything that needs *name*, transitively, as ``{source: children}``."""
        return self.subgraph(name, direction="in", depth=depth)

    def transitive_requires(self, name: str, *, depth: int = MAX_DEPTH) -> tuple[str, ...]:
        """Display names of everything *name* needs, excluding *name* itself."""
        root = normalize_name(name)
        reached = self.requires_tree(name, depth=depth)
        return tuple(self.name_of(key) for key in reached if key != root)

    def transitive_required_by(self, name: str, *, depth: int = MAX_DEPTH) -> tuple[str, ...]:
        """Display names of everything that needs *name*, excluding *name* itself."""
        root = normalize_name(name)
        reached = self.required_by_tree(name, depth=depth)
        return tuple(self.name_of(key) for key in reached if key != root)

    def dependents_of(self, names: str | tuple[str, ...]) -> tuple[str, ...]:
        """Everything that would be affected by removing *names*.

        A package that requires the group *is* affected even when it does not
        require the package directly, so the walk is transitive. The requested
        names themselves are never included.
        """
        group = (names,) if isinstance(names, str) else names
        keys = {normalize_name(item) for item in group}

        affected: dict[str, None] = {}
        for item in group:
            for key in self.transitive_required_by(item):
                if key in keys or key in affected:
                    continue
                affected[key] = None

        return tuple(self.name_of(key) for key in affected)

    def why_installed(self, name: str) -> tuple[str, ...]:
        """The shortest chain from a package the user asked for down to *name*.

        Returned root-first, e.g. ``("httpie", "requests", "urllib3")`` for
        urllib3: the user asked for httpie, httpie asked for requests, requests
        asked for urllib3. An empty tuple means the package is either not
        installed or not reachable from anything the user asked for - which is
        what :meth:`orphans` is about, so the two answers agree by construction.
        """
        root = normalize_name(name)
        target = self.nodes.get(root)
        if target is None:
            return ()
        if target.requested:
            return (target.name,)

        # Breadth-first from every requested package at once, so the chain that
        # arrives first is a shortest one and ties break deterministically.
        parents: dict[str, str] = {}
        visited: set[str] = set()
        frontier = sorted(key for key, node in self.nodes.items() if node.requested)
        visited.update(frontier)

        while frontier:
            next_frontier: list[str] = []
            for key in frontier:
                for child in self.neighbours(key, "out"):
                    if child not in self.nodes or child in visited:
                        continue
                    visited.add(child)
                    parents[child] = key
                    if child == root:
                        return self._chain(root, parents)
                    next_frontier.append(child)
            frontier = next_frontier

        return ()

    def _chain(self, root: str, parents: Mapping[str, str]) -> tuple[str, ...]:
        chain = [root]
        seen = {root}
        current = root
        while current in parents:
            current = parents[current]
            if current in seen:
                break
            seen.add(current)
            chain.append(current)
        chain.reverse()
        return tuple(self.name_of(key) for key in chain)

    def broken(self) -> tuple[str, ...]:
        """Display names of packages whose own requirements are not installed.

        A missing requirement is not an edge, so it would otherwise be invisible
        in every view built on this graph - and an environment in that state is
        exactly what a user opens a dependency view to find.
        """
        return tuple(node.name for node in self.nodes.values() if node.missing)

    # -- clean-up --------------------------------------------------------------

    def orphans(self, *, sizes: Mapping[str, int] | None = None) -> OrphanReport:
        """Packages nothing requires and the user never asked for.

        *sizes* maps a normalised package name to a measured size in bytes, so the
        report can say how much space a clean-up would reclaim. A name missing
        from it is counted as unmeasured rather than as zero, so a total is never
        presented as complete when it is not.
        """
        orphans: list[str] = []
        requested_only: list[str] = []

        for key, node in self.nodes.items():
            if node.required_by:
                continue
            (requested_only if node.requested else orphans).append(key)

        total = 0
        measured = 0
        if sizes:
            for key in orphans:
                size = sizes.get(key)
                if size is not None:
                    measured += 1
                    total += size

        # Sorted by display name rather than left in scan order, so the report a
        # user reads does not depend on the order a directory happened to be read.
        return OrphanReport(
            orphans=tuple(sorted(self.name_of(key) for key in orphans)),
            requested_only=tuple(sorted(self.name_of(key) for key in requested_only)),
            orphan_size_bytes=total,
            measured=measured,
        )

    def preview_removal(self, names: str | tuple[str, ...]) -> RemovalPreview:
        """What removing *names* would do, without removing anything."""
        group = (names,) if isinstance(names, str) else names
        keys = [normalize_name(item) for item in group if normalize_name(item)]
        chosen = {key for key in keys if key in self.nodes}

        blocked: list[str] = []
        breaks: dict[str, None] = {}

        for key in sorted(chosen):
            node = self.nodes[key]
            for dependent in node.required_by:
                if dependent in chosen:
                    continue
                blocked.append(key)
                breaks[dependent] = None

        # A package outside the request whose last dependent is being removed is
        # left installed but unused. Reported rather than swept up: whether it
        # should also go is the user's call, not the preview's.
        leave_unused: list[str] = []
        for key, node in self.nodes.items():
            if key in chosen or not node.required_by:
                continue
            if all(dependent in chosen for dependent in node.required_by):
                leave_unused.append(key)

        return RemovalPreview(
            requested=tuple(self.name_of(key) for key in keys),
            would_remove=tuple(self.name_of(key) for key in sorted(chosen)),
            would_leave_unused=tuple(self.name_of(key) for key in sorted(leave_unused)),
            blocked=tuple(self.name_of(key) for key in dict.fromkeys(blocked)),
            breaks=tuple(self.name_of(key) for key in breaks),
        )


def build_graph(
    index: InstalledMetadataIndex,
    *,
    installed_names: Iterable[str] = (),
) -> DependencyGraph:
    """Build the graph from a metadata index, widened by *installed_names*.

    *installed_names* is what a backend reported as installed. It adds nodes for
    distributions whose metadata could not be read, so the graph never silently
    hides a package the package manager knows about. Those nodes are marked
    ``unknown`` and carry no edges: inventing edges for a package whose metadata
    is unreadable would be worse than admitting we do not know.
    """
    nodes: dict[str, DependencyNode] = {}

    for key, record in index.packages.items():
        requires: list[str] = []
        missing: list[str] = []

        for requirement in record.requires:
            required_key = _requirement_key(requirement)
            if not required_key:
                continue
            # An edge exists only between two installed packages; a requirement
            # that is not installed is reported as missing instead.
            if required_key in index.packages:
                requires.append(required_key)
            elif required_key not in missing:
                missing.append(required_key)

        nodes[key] = DependencyNode(
            name=record.name or key,
            key=key,
            version=record.version,
            requested=record.requested,
            requires=tuple(sorted(set(requires))),
            missing=tuple(sorted(set(missing))),
        )

    for raw_name in installed_names:
        key = normalize_name(raw_name)
        if key and key not in nodes:
            nodes[key] = DependencyNode(
                name=raw_name,
                key=key,
                version="",
                requested=False,
                unknown=True,
            )

    # The reverse edges are derived by inverting the forward ones rather than
    # read from the index, so the two directions of the same relationship cannot
    # disagree - in either direction of read. It also means a package whose own
    # metadata was unreadable still shows what requires it, instead of the
    # reverse view going blind because one file could not be parsed.
    reverse: dict[str, set[str]] = {}
    for key, node in nodes.items():
        for child in node.requires:
            reverse.setdefault(child, set()).add(key)

    complete: dict[str, DependencyNode] = {}
    for key, node in nodes.items():
        dependents = tuple(sorted(reverse.get(key, ())))
        complete[key] = DependencyNode(
            name=node.name,
            key=node.key,
            version=node.version,
            requested=node.requested,
            requires=node.requires,
            required_by=dependents,
            missing=node.missing,
            unknown=node.unknown,
        )

    return DependencyGraph(environment=index.environment, nodes=complete)


def _requirement_key(requirement: str) -> str:
    """The normalised distribution name a ``Requires-Dist`` entry names."""
    try:
        return normalize_name(Requirement(requirement).name)
    except InvalidRequirement:
        # ``metadata`` validates requirements on the way in, so this is a guard
        # rather than a path: an entry that cannot be parsed contributes no edge.
        return ""
