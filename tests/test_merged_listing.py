"""Whose version is the truth when two managers report the same package.

The bug this file exists for came from a real machine: pip had been upgraded to
26.2.1 in the user site directory - where pip puts an upgrade when the
all-users site-packages under ``Program Files`` is not writable - while the
system copy stayed at 24.3.1. uv's listing reads the prefix only, so it kept
reporting 24.3.1; pip's own listing runs the environment's interpreter and
answers in ``sys.path`` order, which is the user-site copy. The merged listing
believed uv first, so the packages table showed pip 24.3.1, PyPI said 26.2.1,
and the badge read "outdated" no matter how many times the package was
upgraded.

The backends are faked through the factory the service uses, so no test here
spawns a package manager; the service's own merge logic is what is under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pypackhub.core.backends.base import (
    Backend,
    BackendCapability,
    BackendError,
    InstalledPackage,
    PackageListing,
)
from pypackhub.core.inventory import InventoryService, PackageStatus


class ListingBackend(Backend):
    """A backend whose listing reports a fixed set of versions.

    *packages* maps package names to versions; *fail* turns the listing into
    the shape of a backend that cannot run in this environment at all.
    """

    def __init__(self, name: str, packages: dict[str, str], *, fail: bool = False) -> None:
        self._name = name
        self._packages = packages
        self._fail = fail

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(name=self._name, can_list=True)

    @property
    def executable_name(self) -> str:
        return self._name

    def executable_path(self) -> Path:
        return Path(f"fake-{self._name}")

    def plan(self, action: Any) -> Any:
        """Listing is all this backend is for; no test plans an action."""
        raise AssertionError("a listing test must not plan an action")

    def list_packages(
        self, environment: Path, *, facts: Any = None, cancel: Any = None
    ) -> PackageListing:
        if self._fail:
            raise BackendError(f"{self._name} cannot list this environment")

        packages = tuple(
            InstalledPackage(
                name=name,
                specifier=version,
                location=environment,
                backend=self._name,
            )
            for name, version in sorted(self._packages.items())
        )
        return PackageListing(
            environment=environment,
            backend=self._name,
            packages=packages,
        )


class FakeIndex:
    """The package index, answering from a dict of latest versions."""

    def __init__(self, latest: dict[str, str]) -> None:
        self._latest = latest

    def info(self, package: str) -> Any:
        return type(
            "Info",
            (),
            {
                "latest_version": self._latest.get(package, ""),
                "summary": "a package",
                "home_page": "",
                "project_url": "",
                "requires_python": "",
                "error": "",
            },
        )()


def install_backends(monkeypatch: pytest.MonkeyPatch, **backends: ListingBackend) -> None:
    """Point the service's backend factory at the given fakes, by name."""
    monkeypatch.setattr(
        "pypackhub.core.inventory._make_backend",
        lambda name, prefix, interpreter: backends[name],
    )


def test_the_interpreters_own_listing_wins_the_version_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: uv's stale system copy used to win the merge.

    This is the machine that reported the bug: uv reports the 24.3.1 that is
    still in ``Program Files``, pip reports the 26.2.1 the upgrade installed
    into the user site - and the table has to show 26.2.1, because that is the
    copy ``import pip`` in that environment actually finds.
    """
    install_backends(
        monkeypatch,
        uv=ListingBackend("uv", {"pip": "24.3.1"}),
        pip=ListingBackend("pip", {"pip": "26.2.1"}),
    )

    row = InventoryService(prefix=tmp_path).list().row("pip")

    assert row is not None
    assert row.installed == "26.2.1"
    assert row.backend == "pip"


def test_the_upgraded_row_turns_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The user-visible end of the bug: upgraded, and *still* outdated.

    Before the fix this row came back OUTDATED - installed 24.3.1 (uv's stale
    copy), latest 26.2.1 - no matter how many times the package was upgraded.
    """
    install_backends(
        monkeypatch,
        uv=ListingBackend("uv", {"pip": "24.3.1"}),
        pip=ListingBackend("pip", {"pip": "26.2.1"}),
    )
    service = InventoryService(prefix=tmp_path, pypi_client=FakeIndex({"pip": "26.2.1"}))

    row = service.refresh(with_sizes=False, with_latest=True).row("pip")

    assert row is not None
    assert row.installed == "26.2.1"
    assert row.latest == "26.2.1"
    assert row.status is PackageStatus.UP_TO_DATE


def test_a_package_only_the_other_manager_reports_stays_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge exists so one manager cannot hide packages; it still must not.

    A uv-managed environment with no pip of its own is the case the merge was
    built for: uv-only packages remain visible even though the pip listing is
    the one the merge now trusts first.
    """
    install_backends(
        monkeypatch,
        uv=ListingBackend("uv", {"pip": "24.3.1", "wheel": "0.45.0"}),
        pip=ListingBackend("pip", {"pip": "26.2.1"}),
    )

    inventory = InventoryService(prefix=tmp_path).list()

    assert inventory.row("pip").installed == "26.2.1"
    assert inventory.row("wheel").installed == "0.45.0"


def test_agreeing_backends_still_merge_plainly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No conflict, no visible change: the common case stays boring."""
    install_backends(
        monkeypatch,
        uv=ListingBackend("uv", {"requests": "2.31.0"}),
        pip=ListingBackend("pip", {"requests": "2.31.0"}),
    )

    inventory = InventoryService(prefix=tmp_path).list()

    assert inventory.row("requests").installed == "2.31.0"


def test_a_broken_listing_does_not_hide_the_working_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pip failing to list (uv-managed environment, no pip) leaves uv's answer."""
    install_backends(
        monkeypatch,
        uv=ListingBackend("uv", {"wheel": "0.45.0"}),
        pip=ListingBackend("pip", {}, fail=True),
    )

    inventory = InventoryService(prefix=tmp_path).list()

    assert inventory.row("wheel").installed == "0.45.0"
    assert "pip" in inventory.error
