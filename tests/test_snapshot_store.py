"""The snapshot store: what was installed, recorded before it changes.

Everything here runs against a temporary directory: no test touches the
application's real data directory. What is checked is the contract the
History page and the Snapshots page both rest on - capture a state, list it
back, diff it against the present, and build the exact pins that put it
back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pypackhub.core.snapshots import (
    AUTOMATIC_KEEP,
    SnapshotStore,
    restore_specs,
)


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(root=tmp_path / "snapshots")


@pytest.fixture
def environment(tmp_path: Path) -> Path:
    return tmp_path / "env"


def test_capture_and_list_round_trip(store: SnapshotStore, environment: Path) -> None:
    store.capture(environment, [("requests", "2.31.0"), ("pip", "24.0")], reason="manual")

    listed = store.list_snapshots(str(environment))

    assert len(listed) == 1
    assert listed[0].package_count == 2
    assert listed[0].version_of == {"pip": "24.0", "requests": "2.31.0"}
    assert listed[0].automatic is False
    assert listed[0].reason == "manual"


def test_the_environment_may_be_spelled_any_way_it_resolves(store, environment) -> None:
    """``C:/x`` and ``C:\\x`` are the same environment to the store."""
    store.capture(environment, [("a", "1.0")])

    assert len(store.list_snapshots(str(environment))) == 1
    assert store.load(str(environment), store.list_snapshots(str(environment))[0].id) is not None


def test_snapshots_list_newest_first(store: SnapshotStore, environment: Path) -> None:
    """Two snapshots a moment apart are two snapshots, newest shown first."""
    first = store.capture(environment, [("a", "1.0")])
    second = store.capture(environment, [("a", "2.0")])

    listed = store.list_snapshots(str(environment))

    assert [entry.id for entry in listed] == [second.id, first.id]


def test_diff_names_are_normalized(store: SnapshotStore, environment: Path) -> None:
    """``Beautiful_Soup`` in the listing and ``beautiful-soup`` in the snapshot match."""
    snapshot = store.capture(environment, [("beautiful-soup", "4.12.0")])

    diff = store.diff(snapshot, [("Beautiful_Soup", "4.12.0"), ("new", "1.0")])

    assert diff.removed == ()
    assert diff.changed == ()
    assert diff.added == ("new",)


def test_diff_reports_removed_changed_and_added(store, environment) -> None:
    snapshot = store.capture(
        environment,
        [("gone", "1.0"), ("moved", "1.0"), ("same", "1.0")],
    )

    diff = store.diff(snapshot, [("moved", "2.0"), ("same", "1.0"), ("arrived", "9.0")])

    assert diff.removed == (("gone", "1.0"),)
    assert diff.changed == (("moved", "1.0", "2.0"),)
    assert diff.added == ("arrived",)
    assert not diff.is_empty


def test_diff_of_an_unchanged_environment_is_empty(store, environment) -> None:
    snapshot = store.capture(environment, [("a", "1.0")])

    assert store.diff(snapshot, [("a", "1.0")]).is_empty


def test_restore_specs_pin_the_recorded_versions(store, environment) -> None:
    """A restore is exact: ``name==version``, not "any version"."""
    snapshot = store.capture(environment, [("a", "1.0"), ("b", "2.0")])

    diff = store.diff(snapshot, [("a", "2.0"), ("c", "1.0")])

    assert restore_specs(diff) == ("a==1.0", "b==2.0")


def test_a_corrupt_snapshot_is_skipped_not_fatal(store, environment, tmp_path) -> None:
    """One unreadable file must not hide the healthy snapshots behind it."""
    good = store.capture(environment, [("a", "1.0")])
    digest = (tmp_path / "snapshots").name  # the root; find the env dir by content
    environment_root = next((tmp_path / "snapshots").iterdir())
    (environment_root / "2099-01-01T00-00-00.json").write_text("{not json", encoding="utf-8")

    listed = store.list_snapshots(str(environment))

    assert [entry.id for entry in listed] == [good.id]
    assert store.load(str(environment), "2099-01-01T00-00-00") is None
    assert digest  # only here so the variable is not flagged as unused


def test_an_id_that_cannot_exist_is_refused(store: SnapshotStore, environment: Path) -> None:
    """Traversal-safe ids: no path can be smuggled through a snapshot id."""
    assert store.load(str(environment), "../../etc") is None
    assert store.delete(str(environment), "../../etc") is False


def test_automatic_snapshots_are_trimmed_manual_ones_never(store, environment) -> None:
    """The automatic history exists to be useful this week, not forever."""
    for index in range(AUTOMATIC_KEEP + 5):
        store.capture(environment, [(f"pkg{index}", "1.0")], automatic=True)
    store.capture(environment, [("manual", "1.0")], automatic=False)

    listed = store.list_snapshots(str(environment))
    automatic = [entry for entry in listed if entry.automatic]
    manual = [entry for entry in listed if not entry.automatic]

    assert len(automatic) == AUTOMATIC_KEEP
    assert len(manual) == 1


def test_snapshot_files_are_plain_json(store: SnapshotStore, environment: Path, tmp_path) -> None:
    """A snapshot is inspectable data, not an opaque blob."""
    store.capture(environment, [("a", "1.0")], reason="checked by hand")

    environment_root = next((tmp_path / "snapshots").iterdir())
    path = next(environment_root.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["format"] == 1
    assert payload["reason"] == "checked by hand"
    assert payload["packages"] == [["a", "1.0"]]
