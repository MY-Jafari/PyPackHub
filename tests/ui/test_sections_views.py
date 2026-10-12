"""The five section pages, driven end to end over fakes.

Search, Security, Snapshots, History and the Dashboard are exercised here
the way the window drives them: workers run on the real task manager, the
network and the package managers are faked, and every claim is about what
the user can see - rows, buttons, emitted actions. No test touches the
application's real data directory: the snapshot stores and the index
clients are injected.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from pypackhub.core.backends.base import ActionKind, PackageAction
from pypackhub.core.inventory import Inventory, PackageRow, PackageStatus
from pypackhub.core.runner import CommandResult
from pypackhub.core.security import ScanReport, Vulnerability
from pypackhub.core.snapshots import SnapshotStore
from pypackhub.core.tasks import TaskManager
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.dashboard import DashboardView
from pypackhub.ui.views.history import HistoryEntryItem, HistoryView
from pypackhub.ui.views.search import SearchResultItem, SearchView
from pypackhub.ui.views.security import SecurityView, VulnerabilityItem
from pypackhub.ui.views.snapshots import SnapshotItem, SnapshotView
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell

pytestmark = pytest.mark.gui

PREFIX = Path("/fake/env").resolve()


# -- fakes ----------------------------------------------------------------------


class FakeSearchIndex:
    """Answers lookups from a dict, shaped the way PyPIClient answers."""

    def __init__(self, answers: dict[str, dict[str, object]]) -> None:
        self._answers = answers

    def meta(self, package: str) -> object:
        answer = self._answers.get(package, {})

        class _Info:
            latest_version = str(answer.get("latest", ""))
            summary = str(answer.get("summary", ""))
            error = "" if package in self._answers else "offline"

        class _Meta:
            info = _Info
            versions = tuple(answer.get("versions", ()))

        return _Meta()


class FakeSearchService:
    """Stand-in for :class:`SearchService`: the same ``search`` contract."""

    def __init__(self, results: list[object]) -> None:
        self._results = results
        self.queries: list[str] = []

    def search(self, query: str, *, cancel=None, on_result=None) -> list[object]:
        self.queries.append(query)
        for result in self._results:
            if on_result is not None:
                on_result(result)
        return self._results


class FakeOsvClient:
    """Stand-in for :class:`OsvClient`: the same ``scan`` contract."""

    def __init__(self, report: ScanReport) -> None:
        self._report = report
        self.scanned: list[object] = []

    def scan(self, pairs, *, cancel=None, on_finding=None) -> ScanReport:
        self.scanned.append(tuple(pairs))
        for finding in self._report.findings:
            if on_finding is not None:
                on_finding(finding)
        return self._report


@dataclass
class FakeListedInventory:
    """The service a section view needs: a listing, nothing more."""

    rows: tuple[tuple[str, str], ...] = (("requests", "2.31.0"), ("pip", "24.0"))

    def list(self) -> Inventory:
        return Inventory(
            environment=PREFIX,
            backend="pip",
            rows=tuple(PackageRow(name=n, installed=v) for n, v in self.rows),
        )


class SingleEnvironmentProvider:
    """Every environment asked for gets the same fake service."""

    def __init__(self, service: object) -> None:
        self.service = service

    def create(self, prefix: Path) -> object:
        return self.service

    def environments(self) -> tuple[Path, ...]:
        return (PREFIX,)


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(root=tmp_path / "snapshots")


@pytest.fixture
def shell(tmp_path: Path) -> WindowShell:
    shell = WindowShell(store=QSettingsStore(path=tmp_path / "settings.ini"))
    shell.load()
    return shell


@pytest.fixture
def context() -> UiContext:
    return UiContext(
        provider=SingleEnvironmentProvider(FakeListedInventory()),
        tasks=TaskManager(history_limit=32),
        default_environment=PREFIX,
    )


# -- search ----------------------------------------------------------------------


def _fake_result(name: str, latest: str = "1.0", summary: str = "a package") -> object:
    return type(
        "Result", (), {"name": name, "latest_version": latest, "summary": summary, "error": ""}
    )()


def test_search_fills_results_and_installs_the_pinned_choice(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    service = FakeSearchService([_fake_result("flask", "3.1.3", "micro framework")])
    client = FakeSearchIndex(
        {
            "flask": {
                "latest": "3.1.3",
                "summary": "micro framework",
                "versions": ("3.1.3", "3.0.0"),
            }
        }
    )
    view = SearchView(context=context, shell=shell, search_service=service, client=client)  # type: ignore[arg-type]

    view.query_edit.setText("flask")
    view.search()
    qtbot.waitUntil(lambda: view.results_list.count() == 1, timeout=5000)
    qtbot.waitUntil(lambda: not view._task, timeout=5000)

    view.results_list.setCurrentRow(0)
    assert isinstance(view.results_list.item(0), SearchResultItem)
    qtbot.waitUntil(lambda: view.version_combo.count() == 3, timeout=5000)

    installed: list[PackageAction] = []
    view.install_requested.connect(installed.append)

    view.version_combo.setCurrentIndex(1)  # the first real version: 3.1.3
    view._install_selected()

    assert len(installed) == 1
    assert installed[0].kind is ActionKind.INSTALL
    assert installed[0].specs == ("flask==3.1.3",)
    assert installed[0].environment == PREFIX


def test_the_unpinned_choice_installs_by_name(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    service = FakeSearchService([_fake_result("flask", "3.1.3")])
    view = SearchView(context=context, shell=shell, search_service=service)  # type: ignore[arg-type]

    view.query_edit.setText("flask")
    view.search()
    qtbot.waitUntil(lambda: view.results_list.count() == 1, timeout=5000)
    view.results_list.setCurrentRow(0)
    qtbot.waitUntil(lambda: view.version_combo.count() >= 1, timeout=5000)

    installed: list[PackageAction] = []
    view.install_requested.connect(installed.append)
    view._install_selected()

    assert installed[0].specs == ("flask",)


# -- security --------------------------------------------------------------------


def _report(findings: list[Vulnerability]) -> ScanReport:
    return ScanReport(
        scanned=(("widget", "2.5.0"),),
        findings=tuple(findings),
        timestamp=datetime(2026, 10, 12, 12, 0, 0).timestamp(),
    )


def test_a_scan_lists_findings_and_fixes_emit_upgrades(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    finding = Vulnerability(
        package="requests",
        installed_version="2.31.0",
        advisory_id="GHSA-1111-aaaa",
        summary="bad",
        url="https://osv.dev/vulnerability/GHSA-1111-aaaa",
        fixed_in="2.32.4",
        severity="high",
    )
    client = FakeOsvClient(_report([finding]))
    view = SecurityView(context=context, shell=shell, osv_client=client)  # type: ignore[arg-type]

    view.start_scan()
    qtbot.waitUntil(lambda: view.report is not None, timeout=5000)

    assert view.vulnerable_count == 1
    assert view.findings_list.count() == 1
    assert isinstance(view.findings_list.item(0), VulnerabilityItem)

    view.findings_list.setCurrentRow(0)
    assert view.fix_button.isEnabled()

    fixes: list[PackageAction] = []
    view.fix_requested.connect(fixes.append)
    view._upgrade_selected()

    assert len(fixes) == 1
    assert fixes[0].kind is ActionKind.UPGRADE
    assert fixes[0].names == ("requests",)
    # Only names and versions were asked about - nothing else leaves the machine.
    assert client.scanned == [(("requests", "2.31.0"), ("pip", "24.0"))]


def test_a_clean_scan_shows_the_empty_state(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    view = SecurityView(context=context, shell=shell, osv_client=FakeOsvClient(_report([])))

    view.start_scan()
    qtbot.waitUntil(lambda: view.report is not None, timeout=5000)

    assert view.vulnerable_count == 0
    assert view.empty_label.isVisibleTo(view)


# -- snapshots -------------------------------------------------------------------


def test_create_lists_and_rolls_back(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell, store: SnapshotStore
) -> None:
    del qapp
    view = SnapshotView(context=context, shell=shell, store=store)

    view._create_clicked()
    qtbot.waitUntil(lambda: view.snapshots_list.count() == 1, timeout=5000)
    assert isinstance(view.snapshots_list.item(0), SnapshotItem)
    assert view.snapshots_list.item(0).snapshot.package_count == 2

    requested: list[tuple[str, str]] = []
    view.rollback_requested.connect(lambda env, sid: requested.append((env, sid)))

    view.snapshots_list.setCurrentRow(0)
    assert view.restore_button.isEnabled()
    view._restore_clicked()

    assert len(requested) == 1
    environment, snapshot_id = requested[0]
    assert environment == str(PREFIX)
    assert store.load(environment, snapshot_id) is not None


def test_delete_removes_the_snapshot(
    qtbot,
    qapp: QApplication,
    context: UiContext,
    shell: WindowShell,
    store: SnapshotStore,
    monkeypatch,
) -> None:
    del qapp
    from PySide6.QtWidgets import QMessageBox

    view = SnapshotView(context=context, shell=shell, store=store)
    snapshot = store.capture(PREFIX, [("requests", "2.31.0")], reason="manual")
    view._render([snapshot])

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    view.snapshots_list.setCurrentRow(0)
    view._delete_clicked()

    qtbot.waitUntil(lambda: view.snapshots_list.count() == 0, timeout=5000)
    assert store.list_snapshots(str(PREFIX)) == []


# -- history ---------------------------------------------------------------------


def _recorded_task(context: UiContext, *, kind: str, names: list[str], snapshot_id: str) -> None:
    """Run one fake changing action through the task manager, with context."""
    context.tasks.schedule(
        f"{kind} requests",
        lambda *_args: CommandResult(
            argv=("pip", kind, "--", *names), returncode=0, stdout="", stderr="", duration=0.0
        ),
        meta={"kind": kind, "names": names, "environment": str(PREFIX), "snapshot": snapshot_id},
        on_finish=lambda _id, _result: None,
    )


def _wait_for_history(context: UiContext, count: int) -> None:
    deadline = time.monotonic() + 5.0
    while len(context.tasks.history()) < count:
        assert time.monotonic() < deadline, "the recorded task did not finish in time"
        threading.Event().wait(0.02)


def test_history_lists_actions_and_offers_undo(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    _recorded_task(
        context, kind="upgrade", names=["requests"], snapshot_id="2026-01-01T00-00-00-000"
    )
    _wait_for_history(context, 1)

    view = HistoryView(context=context, shell=shell)
    view.refresh()

    assert view.history_list.count() == 1
    assert isinstance(view.history_list.item(0), HistoryEntryItem)

    view.history_list.setCurrentRow(0)
    assert view.undo_button.isEnabled()

    undos: list[dict] = []
    view.undo_requested.connect(undos.append)
    view._undo_selected()

    assert len(undos) == 1
    assert undos[0]["names"] == ["requests"]
    assert undos[0]["snapshot"] == "2026-01-01T00-00-00-000"


def test_an_action_without_a_snapshot_cannot_be_undone(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    _recorded_task(context, kind="install", names=["left-pad"], snapshot_id="")

    view = HistoryView(context=context, shell=shell)
    view.refresh()

    view.history_list.setCurrentRow(0)
    assert not view.undo_button.isEnabled()


# -- dashboard ---------------------------------------------------------------------


def _inventory() -> Inventory:
    return Inventory(
        environment=PREFIX,
        backend="pip",
        rows=(
            PackageRow(
                name="numpy",
                installed="2.0.0",
                latest="2.1.0",
                status=PackageStatus.OUTDATED,
                size_bytes=2048,
            ),
            PackageRow(
                name="pandas",
                installed="3.0.0",
                latest="3.0.0",
                status=PackageStatus.UP_TO_DATE,
                size_bytes=4096,
            ),
        ),
    )


def test_the_dashboard_shows_real_numbers(
    qtbot, qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    view = DashboardView(context=context, shell=shell)

    view.set_data(_inventory(), vulnerable_count=2, vulnerable_time="12:00:00")
    view._orphan_count = 1
    view._render()

    values = view._values
    assert values[TranslationKey.DASHBOARD_TOTAL_PACKAGES].text() == "2"
    assert values[TranslationKey.DASHBOARD_OUTDATED_PACKAGES].text() == "1"
    assert values[TranslationKey.DASHBOARD_ORPHANS].text() == "1"
    assert values[TranslationKey.DASHBOARD_VULNERABLE].text() == "2"
    assert values[TranslationKey.DASHBOARD_SIZE].text() == format_bytes(2048 + 4096)
    # The guess follows the packages: numpy and pandas say data science.
    assert values[TranslationKey.DASHBOARD_PURPOSE].text() == "Data science"
    assert "12:00:00" in view.status_label.text()


def test_the_dashboard_without_data_shows_placeholders_not_lies(
    qapp: QApplication, context: UiContext, shell: WindowShell
) -> None:
    del qapp
    view = DashboardView(context=context, shell=shell)

    view.set_data(None)

    values = view._values
    assert values[TranslationKey.DASHBOARD_TOTAL_PACKAGES].text() == "0"
    assert values[TranslationKey.DASHBOARD_OUTDATED_PACKAGES].text() == "—"
    assert values[TranslationKey.DASHBOARD_VULNERABLE].text() == "—"
    assert values[TranslationKey.DASHBOARD_PURPOSE].text() == "General purpose"
