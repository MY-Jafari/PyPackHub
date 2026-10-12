"""What "latest version" means, and the row that could never turn green.

PyPI's JSON document reports the project's newest *upload* as its version -
pre-release included - and this application used to take that field at face
value. ``pip install --upgrade`` and ``uv pip install --upgrade`` never install
a pre-release unless ``--pre`` is passed, so for any package whose newest
upload is an rc or a dev build:

* the upgrade command runs, installs nothing ("Requirement already
  satisfied"), and exits 0 - the application reports success;
* the installed version column cannot change, because there is no newer stable
  release to change to;
* the badge still compares the installed version against the pre-release and
  reads "outdated", so the row sits there, freshly "upgraded" and still yellow.

That is exactly the report "I updated the package and the application still
shows the old version, still marked outdated". The latest version is therefore
resolved the way the resolver resolves candidates: the newest release that has
at least one un-yanked file and is not a pre-release.

The client is exercised through an injected transport, so these tests never
touch the network (the same pattern as the network-policy suite).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from pypackhub.core.inventory import Inventory, InventoryService, PackageRow, PackageStatus
from pypackhub.core.net import HttpClient
from pypackhub.core.pypi import PyPIClient

#: A payload shaped like PyPI's ``/pypi/{name}/json``, with the newest upload a
#: pre-release - the situation that produced the permanently-outdated row.
PANDAS_LIKE: dict[str, Any] = {
    "info": {
        "summary": "data analysis",
        "home_page": "",
        "project_url": "",
        "requires_python": ">=3.10",
    },
    "version": "3.1.0rc0",
    "releases": {
        "2.2.5": [{"filename": "pandas-2.2.5-cp313-win_amd64.whl", "yanked": False}],
        "3.1.0rc0": [{"filename": "pandas-3.1.0rc0-cp313-win_amd64.whl", "yanked": False}],
    },
}


def client_for(payload: Any) -> PyPIClient:
    """A PyPI client whose index always answers with *payload*."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    return PyPIClient(http=HttpClient(transport=transport, allow_insecure_http=False))


def test_the_latest_is_the_newest_stable_release() -> None:
    """An rc sitting at the top of the document is not the latest version.

    The whole point of the fix: 2.2.5 is what an upgrade installs, so 2.2.5 is
    what the Latest column must say - not the 3.1.0rc0 PyPI puts first.
    """
    with client_for(PANDAS_LIKE) as client:
        info = client.info("pandas")

    assert info.latest_version == "2.2.5"


def test_a_newest_upload_that_is_stable_is_reported_as_itself() -> None:
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "2.2.6",
        "releases": {
            "2.2.5": [{"filename": "a.whl", "yanked": False}],
            "2.2.6": [{"filename": "b.whl", "yanked": False}],
        },
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == "2.2.6"


def test_a_release_with_only_yanked_files_is_not_a_latest() -> None:
    """A yanked release is the same dead end a pre-release is: pip skips it."""
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "2.2.6",
        "releases": {
            "2.2.5": [{"filename": "a.whl", "yanked": False}],
            "2.2.6": [{"filename": "pulled.whl", "yanked": True}],
        },
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == "2.2.5"


def test_a_package_with_only_prereleases_points_at_the_newest_of_them() -> None:
    """Nothing stable exists, so the newest release is the only honest answer."""
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "3.1.0rc0",
        "releases": {
            "3.1.0rc0": [{"filename": "a.whl", "yanked": False}],
            "3.1.0rc1": [{"filename": "b.whl", "yanked": False}],
        },
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == "3.1.0rc1"


def test_a_release_with_no_files_is_not_installable_and_not_a_latest() -> None:
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "3.0.0",
        "releases": {
            "2.2.5": [{"filename": "a.whl", "yanked": False}],
            "3.0.0": [],
        },
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == "2.2.5"


def test_a_document_without_releases_does_not_invent_a_prerelease_latest() -> None:
    """No list to reason over: an un-installable claim becomes "unknown" instead.

    "Unknown" is honest; "outdated, forever" - the claim the pre-release made
    before this fix - is the bug.
    """
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "3.1.0rc0",
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == ""


def test_a_document_without_releases_believes_a_stable_top_level() -> None:
    payload = {
        "info": {"summary": "", "home_page": "", "project_url": "", "requires_python": ""},
        "version": "2.2.5",
    }

    with client_for(payload) as client:
        info = client.info("pandas")

    assert info.latest_version == "2.2.5"


def test_an_upgraded_row_turns_green_when_the_newest_upload_is_a_prerelease(
    tmp_path: Path,
) -> None:
    """The regression end to end: upgrade to the newest stable, badge follows.

    This is the wiring the packages table uses after every upgrade: the real
    client answers from the payload above, the service enriches the row, and
    the status is what the badge shows. Before the fix this row came back
    OUTDATED with installed 2.2.5 and latest 3.1.0rc0 - freshly upgraded and
    still marked outdated, no matter how many times the user tried.
    """
    service = InventoryService(
        prefix=tmp_path,
        pypi_client=client_for(PANDAS_LIKE),
    )
    inventory = Inventory(
        environment=tmp_path,
        backend="pip",
        rows=(PackageRow(name="pandas", installed="2.2.5", location=tmp_path),),
    )

    enriched = service.enrich_latest(inventory)
    row = enriched.rows[0]

    assert row.installed == "2.2.5"
    assert row.latest == "2.2.5"
    assert row.status is PackageStatus.UP_TO_DATE
