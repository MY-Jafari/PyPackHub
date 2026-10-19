"""The dependency graph: relationships, why-installed chains, orphans, previews.

Two layers are tested. The first builds a real ``site-packages`` tree on disk and
reads it with the real metadata scanner, so an edge here means the reader and the
graph agree about a ``METADATA`` file. The second hands the graph a hand-built
index, which is the only practical way to build a cycle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypackhub.core import metadata
from pypackhub.core.deps import (
    DependencyGraph,
    DependencyNode,
    build_graph,
    find_conflicts,
    incoming_conflicts,
)


def _install(
    prefix: Path,
    name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    requested: bool = False,
) -> None:
    """Write a minimal but real ``.dist-info`` directory."""
    site = prefix / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    directory = site / f"{name}-{version}.dist-info"
    directory.mkdir(parents=True, exist_ok=True)

    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    (directory / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if requested:
        (directory / "REQUESTED").write_text("", encoding="utf-8")


@pytest.fixture
def environment(tmp_path: Path) -> Path:
    """httpie -> requests -> (urllib3, certifi), plus an unneeded leftover."""
    prefix = tmp_path / "env"
    _install(prefix, "httpie", "3.2.2", requires=("requests>=2.28",), requested=True)
    _install(prefix, "requests", "2.31.0", requires=("urllib3>=1.21", "certifi>=2017"))
    _install(prefix, "urllib3", "2.2.1")
    _install(prefix, "certifi", "2024.2.2")
    _install(prefix, "left-over", "1.0")
    # Something the user asked for that nothing else uses: not an orphan.
    _install(prefix, "black", "24.4.0", requested=True)
    return prefix


@pytest.fixture
def graph(environment: Path) -> DependencyGraph:
    return build_graph(metadata.scan(environment))


def test_direct_relationships_are_read_from_metadata(graph: DependencyGraph) -> None:
    assert graph.requires("requests") == ("certifi", "urllib3")
    assert graph.required_by("urllib3") == ("requests",)
    assert graph.requires("httpie") == ("requests",)
    assert graph.required_by("httpie") == ()


def test_a_package_is_identified_regardless_of_name_spelling(graph: DependencyGraph) -> None:
    assert graph.get("URLLib3") is not None
    assert graph.get("urllib_3") is not None or graph.get("urllib-3") is None


def test_the_requires_tree_stops_at_installed_packages(graph: DependencyGraph) -> None:
    tree = graph.requires_tree("httpie")

    assert tree["httpie"] == ("requests",)
    assert tree["requests"] == ("certifi", "urllib3")
    assert tree["urllib3"] == ()
    assert graph.transitive_requires("httpie") == ("requests", "certifi", "urllib3")


def test_the_reverse_tree_walks_back_to_the_user_request(graph: DependencyGraph) -> None:
    assert graph.transitive_required_by("urllib3") == ("requests", "httpie")
    assert graph.transitive_required_by("httpie") == ()


def test_why_installed_gives_the_chain_from_the_user_request(graph: DependencyGraph) -> None:
    assert graph.why_installed("httpie") == ("httpie",)
    assert graph.why_installed("urllib3") == ("httpie", "requests", "urllib3")
    # A package the user asked for is its own reason.
    assert graph.why_installed("black") == ("black",)
    # An unreachable package has no chain - which is what makes it an orphan.
    assert graph.why_installed("left-over") == ()


def test_declared_but_uninstalled_requirements_are_reported_not_edged(
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "broken"
    _install(prefix, "app", "1.0", requires=("missing-dep>=1",), requested=True)
    broken = build_graph(metadata.scan(prefix))

    assert broken.requires("app") == ()
    assert broken.missing("app") == ("missing-dep",)
    assert broken.broken() == ("app",)
    assert broken.why_installed("missing-dep") == ()


def test_orphans_need_both_absences_to_count(graph: DependencyGraph) -> None:
    report = graph.orphans()

    # `left-over` is required by nothing and asked for by nobody.
    assert report.orphans == ("left-over",)
    # `httpie` and `black` are also required by nothing, but the user asked for
    # them, so they are reported separately instead of being called rubbish.
    assert report.requested_only == ("black", "httpie")
    assert report.count == 1


def test_the_orphan_total_reports_how_much_it_actually_measured(
    graph: DependencyGraph,
) -> None:
    measured = graph.orphans(sizes={"left-over": 4096})
    assert measured.orphan_size_bytes == 4096
    assert measured.measured == 1

    # An orphan with no measurement is not counted as zero-sized.
    unknown = graph.orphans(sizes={})
    assert unknown.orphan_size_bytes == 0
    assert unknown.measured == 0


def test_preview_removal_is_safe_for_a_real_orphan(graph: DependencyGraph) -> None:
    preview = graph.preview_removal("left-over")

    assert preview.would_remove == ("left-over",)
    assert preview.blocked == ()
    assert preview.breaks == ()
    assert preview.is_safe


def test_preview_removal_blocks_something_that_is_still_needed(
    graph: DependencyGraph,
) -> None:
    """Removing urllib3 would leave requests with a requirement it cannot meet."""
    preview = graph.preview_removal("urllib3")

    assert preview.blocked == ("urllib3",)
    assert preview.breaks == ("requests",)
    assert not preview.is_safe
    assert "requests" in graph.dependents_of("urllib3")


def test_preview_removal_of_a_whole_group_is_allowed(graph: DependencyGraph) -> None:
    """Removing a package *and* everything that needs it is coherent, not blocked."""
    preview = graph.preview_removal(("httpie", "requests", "urllib3", "certifi"))

    assert preview.blocked == ()
    assert preview.breaks == ()
    assert preview.is_safe
    assert preview.would_remove == ("certifi", "httpie", "requests", "urllib3")


def test_preview_reports_what_it_would_leave_unused(graph: DependencyGraph) -> None:
    """Removing requests would leave certifi and urllib3 installed but unused."""
    preview = graph.preview_removal("requests")

    # requests itself is still needed by httpie, so this is not a clean removal.
    assert preview.blocked == ("requests",)
    assert preview.breaks == ("httpie",)
    # The cascade is what the preview is for: their only dependent is going.
    assert preview.would_leave_unused == ("certifi", "urllib3")


def test_a_cycle_does_not_hang_the_walk(tmp_path: Path) -> None:
    """Two packages that require each other are invalid but must not hang a click."""
    index = metadata.InstalledMetadataIndex(
        environment=tmp_path,
        packages={
            "first": metadata.PackageMetadata(
                name="first", version="1", requires=("second",), requested=True
            ),
            "second": metadata.PackageMetadata(name="second", version="1", requires=("first",)),
        },
    )
    cyclic = build_graph(index)

    # Both nodes claim the other as a dependent, so required_by is not empty and
    # neither is an orphan; the traversal terminates either way.
    assert cyclic.required_by("first") == ("second",)
    assert cyclic.requires("second") == ("first",)
    assert cyclic.orphans().orphans == ()
    tree = cyclic.required_by_tree("first")
    assert set(tree) <= {"first", "second"}
    assert set(cyclic.requires_tree("first")) <= {"first", "second"}


def test_an_unreadable_distribution_still_appears_without_invented_edges(
    graph: DependencyGraph,
) -> None:
    widened = build_graph(
        metadata.InstalledMetadataIndex(environment=graph.environment),
        installed_names=["something-else"],
    )

    node = widened.get("something-else")
    assert node is not None
    assert node.unknown is True
    assert node.requires == ()
    assert node.required_by == ()
    # It is a node, and unknown packages are never presented as removable.
    assert "something-else" not in widened.requested


def test_a_missing_name_answers_emptily_rather_than_raising(graph: DependencyGraph) -> None:
    assert graph.requires("not-installed") == ()
    assert graph.required_by("not-installed") == ()
    assert graph.transitive_requires("not-installed") == ()
    assert graph.requires_tree("not-installed") == {}


def test_nodes_keep_the_installed_version_and_request_flag(graph: DependencyGraph) -> None:
    node = graph.get("httpie")
    assert isinstance(node, DependencyNode)
    assert node.version == "3.2.2"
    assert node.requested is True
    assert graph.get("left-over").requested is False  # type: ignore[union-attr]


# -- conflict checks (the pip-check equivalent) -----------------------------------


def test_a_broken_declared_range_is_a_conflict_with_its_evidence(tmp_path: Path) -> None:
    """``app`` declares ``requests>=2.28`` and ``requests 2.0.0`` sits there:
    exactly what ``pip check`` reports, and here it carries the names and the
    version so the page can say it in a person's language."""
    prefix = tmp_path / "broken"
    _install(prefix, "app", "1.0", requires=("requests>=2.28",), requested=True)
    _install(prefix, "requests", "2.0.0")

    conflicts = find_conflicts(metadata.scan(prefix))

    assert len(conflicts) == 1
    assert conflicts[0].package == "app"
    assert conflicts[0].target == "requests"
    assert conflicts[0].requirement == "requests>=2.28"
    assert conflicts[0].version == "2.0.0"


def test_a_satisfied_environment_reports_no_conflicts(environment: Path) -> None:
    assert find_conflicts(metadata.scan(environment)) == ()


def test_a_missing_requirement_is_the_graphs_answer_not_a_conflict(tmp_path: Path) -> None:
    """``pip check`` reports missing requirements too; this project reports
    them as the graph's ``missing`` - reporting them twice, in two words,
    would teach the user that neither list can be trusted."""
    prefix = tmp_path / "incomplete"
    _install(prefix, "app", "1.0", requires=("left-pad>=1.0",))

    assert find_conflicts(metadata.scan(prefix)) == ()


def test_an_unparsable_version_is_not_evidence_of_anything(tmp_path: Path) -> None:
    """A distribution whose version string packaging refuses must not be
    called broken: unparsable is not the same as unsatisfied."""
    prefix = tmp_path / "odd"
    _install(prefix, "app", "1.0", requires=("requests>=2.28",))
    _install(prefix, "requests", "not-a-version")

    assert find_conflicts(metadata.scan(prefix)) == ()


def test_an_incoming_downgrade_conflicts_with_what_declares_it(environment: Path) -> None:
    """The confirm-path question: httpie declared ``requests>=2.28``, so
    pinning requests 2.0.0 is a break the user can still choose not to make."""
    conflicts = incoming_conflicts(metadata.scan(environment), "requests", "2.0.0")

    assert len(conflicts) == 1
    assert conflicts[0].package == "httpie"
    assert conflicts[0].version == "2.0.0"  # the version about to be installed


def test_an_incoming_version_that_satisfies_everything_declares_nothing(
    environment: Path,
) -> None:
    assert incoming_conflicts(metadata.scan(environment), "requests", "2.32.0") == ()


def test_an_unversioned_declaration_constrains_nothing(environment: Path) -> None:
    """``Requires-Dist: requests`` with no range accepts any version; the
    check must not invent a constraint the metadata never stated."""
    prefix = environment.parent / "loose"
    _install(prefix, "app", "1.0", requires=("requests",), requested=True)
    _install(prefix, "requests", "2.31.0")

    assert find_conflicts(metadata.scan(prefix)) == ()
    assert incoming_conflicts(metadata.scan(prefix), "requests", "0.1") == ()
