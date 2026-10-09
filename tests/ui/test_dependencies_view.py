"""The dependencies view: what it shows, and what it refuses to do itself.

The page is driven from an already-built graph in these tests - the scanning is
the worker's job and is covered by the graph's own tests - so what is checked
here is the rendering and the one rule that matters: previewing a removal never
runs it. The removal leaves the page as a signal for the packages view to
confirm and execute.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog

from pypackhub.core import metadata
from pypackhub.core.backends.base import ActionKind
from pypackhub.core.deps import build_graph
from pypackhub.core.settings import Language
from pypackhub.ui.app import build_window
from pypackhub.ui.i18n.translations import EN_TABLE, FA_TABLE, TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views import ViewName
from pypackhub.ui.views.dependencies import DependenciesView
from pypackhub.ui.views.main_window import MainWindow
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell
from pypackhub.ui.widgets.dialogs import RemovalPreviewDialog

pytestmark = pytest.mark.gui


def _install(
    prefix: Path,
    name: str,
    version: str,
    *,
    requires: tuple[str, ...] = (),
    requested: bool = False,
) -> None:
    site = prefix / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    directory = site / f"{name}-{version}.dist-info"
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    (directory / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if requested:
        (directory / "REQUESTED").write_text("", encoding="utf-8")


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    environment = tmp_path / "env"
    _install(environment, "httpie", "3.2.2", requires=("requests>=2.28",), requested=True)
    _install(environment, "requests", "2.31.0", requires=("urllib3>=1.21",))
    _install(environment, "urllib3", "2.2.1")
    _install(environment, "left-over", "1.0")
    return environment


@pytest.fixture
def shell(tmp_path: Path) -> WindowShell:
    shell = WindowShell(store=QSettingsStore(path=tmp_path / "settings.ini"))
    shell.load()
    return shell


@pytest.fixture
def view(qapp: QApplication, shell: WindowShell, prefix: Path) -> DependenciesView:
    page = DependenciesView(context=UiContext(default_environment=prefix), shell=shell)
    page.show_graph(build_graph(metadata.scan(prefix)))
    return page


def _package_names(view: DependenciesView) -> list[str]:
    return [
        view.package_list.topLevelItem(index).text(0)
        for index in range(view.package_list.topLevelItemCount())
    ]


def _tree_text(tree) -> list[str]:
    """Every item's text in a tree, depth-first."""
    texts: list[str] = []

    def walk(item) -> None:
        texts.append(item.text(0))
        for index in range(item.childCount()):
            walk(item.child(index))

    for index in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(index))
    return texts


# -- the package list ---------------------------------------------------------


def test_every_installed_package_is_listed_with_its_version(view: DependenciesView) -> None:
    names = _package_names(view)
    assert "httpie 3.2.2" in names
    assert "requests 2.31.0" in names
    assert "left-over 1.0" in names
    assert len(names) == 4


def test_the_filter_hides_packages_that_do_not_match(view: DependenciesView) -> None:
    view.search_edit.setText("url")

    visible = [
        view.package_list.topLevelItem(index)
        for index in range(view.package_list.topLevelItemCount())
        if not view.package_list.topLevelItem(index).isHidden()
    ]
    assert [item.text(0) for item in visible] == ["urllib3 2.2.1"]


def test_selecting_a_package_by_name_works_without_a_mouse(view: DependenciesView) -> None:
    assert view.select_package("urllib3") is True
    assert view.select_package("not-installed") is False


# -- why is it installed ------------------------------------------------------


def test_the_reason_is_the_chain_from_what_the_user_asked_for(
    view: DependenciesView,
) -> None:
    view.select_package("urllib3")

    assert view.reason_label.text() == "httpie \u2192 requests \u2192 urllib3"


def test_a_package_the_user_asked_for_says_so(view: DependenciesView) -> None:
    view.select_package("httpie")

    assert view.reason_label.text() == EN_TABLE[TranslationKey.DEPS_WHY_REQUESTED]


def test_an_unneeded_package_is_explained_rather_than_left_blank(
    view: DependenciesView,
) -> None:
    view.select_package("left-over")

    assert view.reason_label.text() == EN_TABLE[TranslationKey.DEPS_WHY_ORPHAN]


# -- the trees ----------------------------------------------------------------


def test_the_requires_tree_nests_the_transitive_requirements(
    view: DependenciesView,
) -> None:
    view.select_package("httpie")

    texts = _tree_text(view.requires_tree)
    assert texts[0] == "httpie"
    assert "requests" in texts
    assert "urllib3" in texts
    assert "left-over" not in texts


def test_the_required_by_tree_walks_back_up_to_the_root(view: DependenciesView) -> None:
    view.select_package("urllib3")

    texts = _tree_text(view.required_by_tree)
    assert texts[0] == "urllib3"
    assert "requests" in texts
    assert "httpie" in texts


def test_a_leaf_says_nothing_rather_than_showing_an_empty_box(
    view: DependenciesView,
) -> None:
    view.select_package("urllib3")

    assert _tree_text(view.requires_tree) == ["urllib3", EN_TABLE[TranslationKey.DEPS_NONE]]


def test_missing_requirements_are_surfaced_not_hidden(
    qapp: QApplication, shell: WindowShell, tmp_path: Path
) -> None:
    environment = tmp_path / "broken"
    _install(environment, "app", "1.0", requires=("gone-dep>=1",), requested=True)
    page = DependenciesView(context=UiContext(default_environment=environment), shell=shell)
    page.show_graph(build_graph(metadata.scan(environment)))

    page.select_package("app")

    # `isHidden` rather than `isVisible`: the page is never shown in this test, so
    # Qt reports every child as not visible regardless of its own state.
    assert page.missing_group.isHidden() is False
    assert page.missing_label.text() == "gone-dep"


def test_the_details_are_empty_until_something_is_selected(view: DependenciesView) -> None:
    assert view.prompt_label.isHidden() is False
    assert view.reason_label.text() == ""
    assert view._groups[TranslationKey.DEPS_WHY].isHidden() is True


# -- orphans ------------------------------------------------------------------


def test_the_orphan_list_shows_only_what_nothing_needs(view: DependenciesView) -> None:
    listed = [view.orphans_list.item(index).text() for index in range(view.orphans_list.count())]
    assert listed == ["left-over"]
    assert view.clean_up_button.isEnabled()


def test_the_summary_counts_the_packages_and_the_unneeded_ones(
    view: DependenciesView,
) -> None:
    assert view.summary_label.text() == EN_TABLE[TranslationKey.DEPS_SUMMARY].replace(
        "%(packages)s", "4"
    ).replace("%(orphans)s", "1")


def test_sizes_are_only_totalled_when_they_were_measured(
    view: DependenciesView, prefix: Path
) -> None:
    assert view.orphans_size_label.isHidden() is True

    view.set_size_source({"left-over": 2048})
    view.show_graph(build_graph(metadata.scan(prefix)))

    assert view.orphans_size_label.isHidden() is False
    assert view.orphans_size_label.text() != ""


# -- the removal flows through the packages view -------------------------------


def test_previewing_a_removal_emits_an_action_and_runs_nothing(
    view: DependenciesView, monkeypatch
) -> None:
    emitted: list[object] = []
    view.removal_requested.connect(emitted.append)
    monkeypatch.setattr(RemovalPreviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    assert view.preview_orphan_removal() is True

    assert len(emitted) == 1
    action = emitted[0]
    assert action.kind is ActionKind.UNINSTALL
    assert action.names == ("left-over",)


def test_declining_the_preview_emits_nothing(view: DependenciesView, monkeypatch) -> None:
    emitted: list[object] = []
    view.removal_requested.connect(emitted.append)
    monkeypatch.setattr(RemovalPreviewDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    assert view.preview_orphan_removal() is False
    assert emitted == []


def test_the_preview_dialog_states_what_the_request_would_leave_behind(
    qapp: QApplication, tmp_path: Path
) -> None:
    from pypackhub.ui.i18n.translator import Translator

    prefix_index = metadata.InstalledMetadataIndex(
        environment=tmp_path,
        packages={
            "app": metadata.PackageMetadata(name="app", version="1", requested=True),
            "lib": metadata.PackageMetadata(
                name="lib", version="1", requires=("app",), requested=True
            ),
        },
    )
    graph = build_graph(prefix_index)
    preview = graph.preview_removal("app")

    dialog = RemovalPreviewDialog(preview=preview, translator=Translator())

    assert dialog.remove_text == "app"
    # lib requires app, so removing app is blocked and that has to be visible
    # before the user is offered the confirmation that would run it.
    assert "app" in dialog.blocked_text
    assert "lib" in dialog.breaks_text
    assert dialog.unused_text == ""


# -- re-translation and wiring ------------------------------------------------


def test_switching_language_re_renders_the_page(view: DependenciesView, shell: WindowShell) -> None:
    shell.set_language(Language.FA)
    view.retranslate()

    assert view.title_label.text() == FA_TABLE[TranslationKey.DEPS_TITLE]
    assert (
        view._groups[TranslationKey.DEPS_ORPHANS].title() == FA_TABLE[TranslationKey.DEPS_ORPHANS]
    )
    assert view.search_edit.placeholderText() == FA_TABLE[TranslationKey.DEPS_SEARCH]


def test_the_window_owns_the_page_and_routes_removals_to_the_packages_view(
    qapp: QApplication, tmp_path: Path
) -> None:
    window: MainWindow = build_window(store=QSettingsStore(path=tmp_path / "settings.ini"))

    assert window.nav_button(ViewName.DEPENDENCIES).isEnabled()
    page = window._dependencies_view

    # The page emits; the window hands it to the packages view, which is still
    # the only thing that shows the command and asks for approval.
    recorded: list[object] = []

    def record(action: object) -> bool:
        recorded.append(action)
        return True

    window.packages_view.confirm_and_run = record  # type: ignore[method-assign]

    environment = window.context.default_environment
    action = window.context.service_for(environment).action_for_uninstall_many(["left-over"])
    page.removal_requested.emit(action)

    assert recorded == [action]
    window.close()
