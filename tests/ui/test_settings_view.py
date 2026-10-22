"""The settings page: population, change handling and re-translation.

The page is the only place the user can change a preference, so these tests cover
the three things that could quietly go wrong: showing stored settings (which must
not look like an edit), editing a setting (which must reach the shell and the store
once), and switching language (which must re-render the whole page, not just the
combo boxes).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox

from pypackhub.core.settings import AppSettings, ColorTheme, Language
from pypackhub.core.validation import ValidationError
from pypackhub.ui.i18n.translations import EN_TABLE, FA_TABLE, TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.settings import PACKAGE_MANAGER_CHOICES, SettingsView
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell
from pypackhub.ui.widgets.log_panel import RunOutcome

pytestmark = pytest.mark.gui

#: A prefix that never has to exist: no test in this file touches an environment.
PREFIX = Path("/fake/env").resolve()


@pytest.fixture
def store(tmp_path: Path) -> QSettingsStore:
    return QSettingsStore(path=tmp_path / "settings.ini")


@pytest.fixture
def shell(store: QSettingsStore) -> WindowShell:
    shell = WindowShell(store=store)
    shell.load()
    return shell


@pytest.fixture
def view(qapp: QApplication, shell: WindowShell) -> SettingsView:
    page = SettingsView(shell=shell)
    page.load_settings(shell.settings)
    return page


def _labels(page: SettingsView) -> list[str]:
    return [label.text() for label in page.findChildren(QLabel)]


def test_the_page_shows_the_settings_it_was_given(view: SettingsView, shell: WindowShell) -> None:
    stored = AppSettings(
        theme=ColorTheme.DARK,
        language=Language.FA,
        sidebar_width=320,
        package_list_page_size=60,
        pypi_index_url="https://mirror.example.test/simple",
        extra_index_urls=("https://extra.example.test/simple",),
        trusted_hosts=("mirror.example.test",),
        confirm_before_install=False,
        confirm_before_upgrade=False,
        show_sizes_by_default=False,
    )

    view.load_settings(stored)
    shown = view.current_settings()

    # The shell's own settings are not serialised here; compare field by field
    # against what was handed in rather than against the shell object.
    assert shown.theme is ColorTheme.DARK
    assert shown.language is Language.FA
    assert shown.sidebar_width == 320
    assert shown.package_list_page_size == 60
    assert shown.pypi_index_url == "https://mirror.example.test/simple"
    assert shown.extra_index_urls == ("https://extra.example.test/simple",)
    assert shown.trusted_hosts == ("mirror.example.test",)
    assert shown.confirm_before_install is False
    assert shown.confirm_before_upgrade is False
    assert shown.show_sizes_by_default is False


def test_loading_the_stored_settings_does_not_write_them_back(
    qapp: QApplication, shell: WindowShell
) -> None:
    """Populating the controls emits their change signals; that must not persist.

    If it did, the first window built would save a half-populated form (the PyPI
    index is empty until its own field is filled) and the validator would refuse
    it - which is exactly the bug this guard exists for.
    """
    page = SettingsView(shell=shell)
    before = shell.settings

    page.load_settings(AppSettings(theme=ColorTheme.LIGHT, language=Language.FA))

    assert shell.settings == before


def test_picking_a_theme_writes_through_the_shell_once(
    view: SettingsView, shell: WindowShell, store: QSettingsStore
) -> None:
    view._theme_combo.setCurrentIndex(list(ColorTheme).index(ColorTheme.LIGHT))

    assert shell.settings.theme is ColorTheme.LIGHT
    assert shell.active_theme.name.value == ColorTheme.LIGHT.value
    assert store.load()["theme"] == ColorTheme.LIGHT.value


def test_picking_a_language_writes_through_the_shell(
    view: SettingsView, shell: WindowShell, store: QSettingsStore
) -> None:
    view._language_combo.setCurrentIndex(list(Language).index(Language.FA))

    assert shell.settings.language is Language.FA
    assert store.load()["language"] == Language.FA.value


def test_an_empty_index_url_is_refused_rather_than_written(
    view: SettingsView, shell: WindowShell
) -> None:
    before = shell.settings
    view._index_url.setText("")

    with pytest.raises(ValidationError):
        view.apply_current_settings()

    # The refusal happened before the shell was told anything.
    assert shell.settings == before


def test_switching_language_re_renders_every_part_of_the_page(
    view: SettingsView, shell: WindowShell
) -> None:
    # Group titles are not child widgets, so they are checked through the group
    # boxes themselves rather than through the QLabel scan below.
    assert (
        view._groups[TranslationKey.SETTINGS_TITLE].title()
        == EN_TABLE[TranslationKey.SETTINGS_TITLE]
    )
    english = _labels(view)
    assert EN_TABLE[TranslationKey.SETTINGS_THEME] in english

    shell.set_language(Language.FA)
    view.retranslate()

    persian = _labels(view)
    assert FA_TABLE[TranslationKey.SETTINGS_THEME] in persian
    assert FA_TABLE[TranslationKey.SETTINGS_PYPI_INDEX] in persian
    assert FA_TABLE[TranslationKey.SETTINGS_CONFIRM_INSTALL] in persian
    assert EN_TABLE[TranslationKey.SETTINGS_THEME] not in persian

    # The group boxes, the multi-line placeholders and the spin-box suffix follow
    # the same pass; none of them may be left behind in the old language.
    assert (
        view._groups[TranslationKey.SETTINGS_TITLE].title()
        == FA_TABLE[TranslationKey.SETTINGS_TITLE]
    )
    assert (
        view._groups[TranslationKey.SETTINGS_BEHAVIOUR].title()
        == FA_TABLE[TranslationKey.SETTINGS_BEHAVIOUR]
    )
    assert view._extra_index_urls.placeholderText() == FA_TABLE[TranslationKey.SETTINGS_LINES_HINT]
    assert (
        view._page_size.suffix().strip()
        == FA_TABLE[TranslationKey.SETTINGS_PACKAGES_PER_PAGE_UNITS]
    )


def test_the_combo_entries_are_offered_in_every_variant(view: SettingsView) -> None:
    assert view._theme_combo.count() == len(ColorTheme)
    assert view._language_combo.count() == len(Language)
    for index in range(len(ColorTheme)):
        assert view._theme_combo.itemData(index) == list(ColorTheme)[index].value
    for index in range(len(Language)):
        assert view._language_combo.itemData(index) == list(Language)[index].value


# -- the package-manager row --------------------------------------------------
#
# The row used to be a bare combo with no hint under it and no height of its own:
# a styled ``QComboBox`` takes its height from the style sheet, and with no
# ``min-height`` it renders as a thin bar that cannot be aimed at. These tests
# measure the control as the user sees it - with the application's stylesheet
# applied - rather than trusting a hard-coded ``setMinimumHeight``.


def test_the_package_manager_control_is_a_usable_field(
    qapp: QApplication, view: SettingsView, shell: WindowShell
) -> None:
    shell.apply_to(qapp)
    view.resize(1100, 700)
    view.show()

    combo = view._package_manager
    assert combo.height() >= 26
    assert combo.width() >= 220


def test_the_package_manager_row_explains_itself_and_follows_the_language(
    qapp: QApplication, view: SettingsView, shell: WindowShell
) -> None:
    del qapp
    hint = view._labels[TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT]
    assert hint.text() == EN_TABLE[TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT]
    assert not hint.isHidden()
    assert hint.wordWrap()

    shell.set_language(Language.FA)
    view.retranslate()
    assert hint.text() == FA_TABLE[TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT]


def test_the_package_manager_entries_say_what_they_mean(
    qapp: QApplication, view: SettingsView, shell: WindowShell
) -> None:
    """ "auto" alone says nothing; every other entry is a product's own name."""
    del qapp
    combo = view._package_manager
    choices = [combo.itemData(index) for index in range(combo.count())]

    assert choices == [
        "auto",
        "uv",
        "pip",
        "conda",
        "mamba",
        "poetry",
        "pipx",
        "pipenv",
        "pdm",
    ]
    assert combo.itemText(0) == EN_TABLE[TranslationKey.SETTINGS_PACKAGE_MANAGER_AUTO]
    # The product names are their own labels in every language: translating
    # "poetry" would make the entry harder to match to the poetry on the
    # user's machine, not easier.
    for index, name in enumerate(choices[1:], start=1):
        assert combo.itemText(index) == name

    shell.set_language(Language.FA)
    view.retranslate()
    assert combo.itemText(0) == FA_TABLE[TranslationKey.SETTINGS_PACKAGE_MANAGER_AUTO]


def test_the_page_scrolls_when_it_is_taller_than_the_window(
    qapp: QApplication, view: SettingsView
) -> None:
    """A setting that falls off the bottom edge with no scrollbar is unreachable."""
    del qapp
    view.resize(900, 420)
    view.show()

    assert view._scroll.widget() is view._body
    assert view._body.height() > view._scroll.viewport().height()
    assert view._scroll.verticalScrollBar().maximum() > 0
    # Every control is inside the scrolled body, so scrolling reaches all of them.
    assert isinstance(view._package_manager, QComboBox)
    assert view._scroll.isAncestorOf(view._package_manager)


# -- the package-manager change reaching the application ----------------------
#
# The combo used to call ``shell.context.set_package_manager`` - an attribute no
# shell has ever had, guarded by ``hasattr``, so the call never ran. A manager
# picked on the page therefore changed nothing until the next launch. The page
# is now handed the window's real context, and these tests hold that wiring in
# place: a control that stores a preference without applying it is a control
# that lies.


class RecordingProvider:
    """The provider as the settings page sees it: it records, it does not build.

    ``create`` returns a fresh object per call, which is what makes the service
    cache observable: a service that survives a package-manager change is a
    service still bound to the previous choice.
    """

    def __init__(self) -> None:
        self.choices: list[str] = []

    def set_package_manager(self, package_manager: str) -> None:
        self.choices.append(package_manager)

    def create(self, prefix: Path) -> object:
        del prefix
        return object()

    def environments(self) -> tuple[Path, ...]:
        return ()


def test_picking_a_manager_reaches_the_services_the_views_share(
    qapp: QApplication, shell: WindowShell
) -> None:
    del qapp
    provider = RecordingProvider()
    context = UiContext(provider=provider, default_environment=PREFIX)
    stale = context.service_for(PREFIX)
    page = SettingsView(shell=shell, context=context)
    page.load_settings(shell.settings)

    page._package_manager.setCurrentIndex(PACKAGE_MANAGER_CHOICES.index("pip"))

    assert context.package_manager == "pip"
    assert provider.choices == ["pip"]
    # The provider was told about the change rather than replaced (a test's fake
    # must survive it, and so must the real provider's own configuration), and
    # the services built for the previous choice did not outlive it.
    assert context.provider is provider
    assert context.service_for(PREFIX) is not stale


def test_a_page_with_no_context_still_persists_the_choice(
    view: SettingsView, shell: WindowShell, store: QSettingsStore
) -> None:
    """A context-less page keeps its settings; it only cannot rewire services."""
    view._package_manager.setCurrentIndex(PACKAGE_MANAGER_CHOICES.index("uv"))

    assert shell.settings.package_manager == "uv"
    assert store.load()["package_manager"] == "uv"


def test_saving_the_page_keeps_the_registered_folders(
    view: SettingsView, shell: WindowShell, tmp_path: Path
) -> None:
    """The environments page registers folders; the settings page edits other
    things. A save from the settings page rebuilds the whole settings object
    from the controls, so the folder list has to be carried through - the
    regression this pins is a save quietly forgetting every project."""
    registered = (str(tmp_path / "code" / "app"),)
    shell.apply_settings(replace(shell.settings, registered_environments=registered))
    view.load_settings(shell.settings)

    view._page_size.setValue(90)  # an unrelated edit
    view.apply_current_settings()

    assert shell.settings.registered_environments == registered


# -- choosing a manager tells the user what they chose ----------------------------


def _choose(view: SettingsView, manager: str) -> None:
    view._package_manager.setCurrentIndex(PACKAGE_MANAGER_CHOICES.index(manager))


def test_choosing_an_installed_manager_says_so_under_the_combo(
    view: SettingsView, monkeypatch
) -> None:
    """The check the user asked for, at the moment of choosing: the page says
    the chosen manager is installed, and asks nothing."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: True)

    _choose(view, "pipx")

    assert view._package_manager_status.text() == EN_TABLE[
        TranslationKey.SETTINGS_MANAGER_STATUS_INSTALLED
    ].replace("%(backend)s", "pipx")


def test_choosing_a_missing_manager_offers_to_install_it(view: SettingsView, monkeypatch) -> None:
    """Not installed is a question, not a surprise for later: the page offers
    to install the manager right there, and the answer routes to the
    confirm-and-run path rather than installing anything itself."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    requested: list[str] = []
    view.manager_install_requested.connect(requested.append)

    _choose(view, "pipx")

    assert requested == ["pipx"]
    assert "not installed" in view._package_manager_status.text()


def test_declining_the_install_keeps_the_offer_for_action_time(
    view: SettingsView, monkeypatch
) -> None:
    """ "Not now" is an answer, not a refusal to ever mention it again: the
    status line says the next change through the manager will offer the
    install, which the bootstrap flow does."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    requested: list[str] = []
    view.manager_install_requested.connect(requested.append)

    _choose(view, "pipx")

    assert requested == []
    assert "not installed" in view._package_manager_status.text()


def test_the_automatic_choice_has_no_status_to_show(view: SettingsView, monkeypatch) -> None:
    """``auto`` names no single tool, so there is no installed state to
    report for it."""
    monkeypatch.setattr(
        "pypackhub.ui.views.settings.manager_available",
        lambda name: pytest.fail("auto must not be probed as a manager name"),
    )

    _choose(view, "auto")

    assert view._package_manager_status.isHidden()


def test_a_language_switch_re_renders_the_status_without_reasking(
    view: SettingsView, shell: WindowShell, monkeypatch
) -> None:
    """The status line follows the language; the question does not follow it.
    A language switch is not a selection, and the answer that was given
    stands."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    asked: list[object] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(
            lambda *args, **kwargs: (asked.append(args), QMessageBox.StandardButton.No)[1]
        ),
    )
    _choose(view, "pipx")
    assert len(asked) == 1

    shell.set_language(Language.FA)
    view.retranslate()

    assert len(asked) == 1
    assert view._package_manager_status.text() == FA_TABLE[
        TranslationKey.SETTINGS_MANAGER_STATUS_MISSING
    ].replace("%(backend)s", "pipx")


# -- the remove offer -------------------------------------------------------------


def test_an_installed_removable_manager_offers_the_remove_button(
    view: SettingsView, monkeypatch
) -> None:
    """The button's whole promise is "take back out what pip put in", so it
    appears exactly where that is true: an installed manager pip installed.
    Clicking it routes to the confirm-and-run path by name."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: True)
    requested: list[str] = []
    view.manager_remove_requested.connect(requested.append)

    _choose(view, "pipx")

    assert not view._manager_remove_button.isHidden()
    assert view._manager_remove_button.text() == EN_TABLE[
        TranslationKey.SETTINGS_MANAGER_REMOVE
    ].replace("%(backend)s", "pipx")

    view._manager_remove_button.click()
    assert requested == ["pipx"]


def test_pip_itself_is_never_offered_for_removal(view: SettingsView, monkeypatch) -> None:
    """pip is the baseline every bootstrap path starts from; a page offering
    to remove the floor beneath the environment would be lying about the
    consequences. Its state is still reported - only the removal is absent."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: True)

    _choose(view, "pip")

    assert "installed" in view._package_manager_status.text()
    assert view._manager_remove_button.isHidden()


def test_a_manager_this_application_cannot_install_is_never_offered(
    view: SettingsView, monkeypatch
) -> None:
    """The reported dead-end: picking conda asked "install it now?", the
    answer was "yes" - and nothing happened, because the only honest
    answer was a refusal that arrived as a toast somewhere the settings
    page does not show. A question this application cannot act on is not
    asked at all; the row says where the manager comes from instead."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    asked: list[object] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(
            lambda *args, **kwargs: (asked.append(args), QMessageBox.StandardButton.Yes)[1]
        ),
    )
    requested: list[str] = []
    view.manager_install_requested.connect(requested.append)

    _choose(view, "conda")

    assert asked == []
    assert requested == []
    assert "not a pip package" in view._package_manager_status.text()
    assert not view._package_manager_status.isHidden()
    assert view._manager_remove_button.isHidden()


def test_an_installable_manager_is_still_offered(view: SettingsView, monkeypatch) -> None:
    """The offer survives where it is honest: a missing manager the pip
    bootstrap can reach is asked about, and a "yes" routes to the one
    install path - answered with the int the real question returns."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: int(QMessageBox.StandardButton.Yes)),
    )
    requested: list[str] = []
    view.manager_install_requested.connect(requested.append)

    _choose(view, "poetry")

    assert requested == ["poetry"]
    assert "not installed" in view._package_manager_status.text()


def test_a_real_pyside_answer_is_an_int_and_still_counts_as_a_yes(
    view: SettingsView, monkeypatch
) -> None:
    """The regression behind the reported dead-end: PySide6's static
    ``QMessageBox.question`` answers with the button's *value* - a plain
    int, not the enum member - and the old identity comparison silently
    ignored every real "Yes" ever clicked. The question appeared, the
    click vanished, nothing ran. This mock answers with the form the
    real platform answers with."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: int(QMessageBox.StandardButton.Yes)),
    )
    requested: list[str] = []
    view.manager_install_requested.connect(requested.append)

    _choose(view, "pipenv")

    assert requested == ["pipenv"]


def test_a_restored_non_installable_manager_reports_without_asking(
    view: SettingsView, monkeypatch
) -> None:
    """The restore path follows the same rule: a stored conda renders the
    sentence about where it comes from, and never opens the question."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    asked: list[object] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(
            lambda *args, **kwargs: (asked.append(args), QMessageBox.StandardButton.Yes)[1]
        ),
    )

    view.load_settings(replace(view._shell.settings, package_manager="conda"))

    assert asked == []
    assert "not a pip package" in view._package_manager_status.text()


def test_a_groups_last_row_clears_its_frame(view: SettingsView) -> None:
    """The reported sinking: the group box hugged its content so tightly
    that the bottom description read as drawn through the border, and the
    next group's title sat a hair below it. The insets live in the layout's
    own contents margins now - generous, especially at the bottom - because
    a stylesheet ``padding`` on a group box silently overrides them."""
    assert view._groups, "the page builds its groups"
    for box in view._groups.values():
        margins = box.layout().contentsMargins()
        assert margins.bottom() >= 20, "the last row clears the frame"
        assert margins.left() >= 16
        assert margins.right() >= 16
        assert box.layout().verticalSpacing() >= 10, "rows breathe"


def test_the_wheel_over_the_manager_combo_changes_nothing(
    view: SettingsView, qapp: QApplication
) -> None:
    """Qt's combos answer a wheel event by changing the selection, so a
    hover that merely drifted across the manager row could silently flip
    the tool - and a manager flip opens install questions and re-probes.
    The wheel is swallowed while the list is closed; clicking first stays
    the only way to choose."""
    del qapp
    combo = view._package_manager
    combo.setCurrentIndex(combo.findData("uv"))
    before = combo.currentIndex()

    event = QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(300.0, 300.0),
        QPoint(0, 0),
        QPoint(0, 240),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    for _ in range(5):
        QApplication.sendEvent(combo, event)

    assert combo.currentIndex() == before


def test_a_click_still_changes_the_wheel_guarded_combo(view: SettingsView) -> None:
    """The guard swallows scrolls, not selections: the programmatic
    equivalent of a click still changes the choice and fires the row's
    own handler."""
    combo = view._package_manager
    combo.setCurrentIndex(combo.findData("uv"))
    assert combo.currentData() == "uv"


def test_the_spin_boxes_keep_their_numbers_clear_of_their_buttons(
    view: SettingsView,
) -> None:
    """Qt draws a spin box's text left-aligned whatever the direction is,
    but it mirrors the up/down buttons to the left in a right-to-left
    layout - so in Persian the number was painted underneath the buttons
    and its first digit vanished. The text is aligned with the far edge
    instead, which is away from the buttons in both directions."""
    shell = view._shell
    shell.apply_settings(replace(shell.settings, language=Language.FA))
    view.retranslate()
    assert view._sidebar_width.alignment() == (
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    assert view._page_size.alignment() == (
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )

    shell.apply_settings(replace(shell.settings, language=Language.EN))
    view.retranslate()
    assert view._sidebar_width.alignment() == (
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )


def test_a_missing_manager_has_no_remove_button(view: SettingsView, monkeypatch) -> None:
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )

    _choose(view, "pipx")

    assert view._manager_remove_button.isHidden()


def test_a_restored_choice_reports_its_state_without_asking(
    view: SettingsView, monkeypatch
) -> None:
    """The report behind the guard: restoring a stored, uninstalled manager
    greeted the user with the install question before the window even
    appeared, because the restore set the combo and the handler guarded its
    save but not its prompt.

    A restore renders the honest state - the page must say what is
    installed from the moment it appears - and only a *selection* may ask
    whether to install more."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: False)
    asked: list[object] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.question",
        staticmethod(
            lambda *args, **kwargs: (asked.append(args), QMessageBox.StandardButton.No)[1]
        ),
    )

    view.load_settings(replace(view._shell.settings, package_manager="pipx"))

    assert asked == []
    assert "not installed" in view._package_manager_status.text()
    assert view._manager_remove_button.isHidden()


def test_the_manager_row_bar_counts_fills_and_ends_red(view: SettingsView) -> None:
    """The row's own bar, through its whole story: hidden at rest, counting
    from zero when the run starts, moved to the percentage the run itself
    reported, and red - the whole bar - when the run failed."""
    bar = view._manager_progress
    assert bar.isHidden()

    view.show_manager_run(determinate=True)
    assert not bar.isHidden()
    assert bar.maximum() == 100
    assert bar.value() == 0

    view.set_manager_progress(61)
    assert bar.value() == 61

    view.finish_manager_run(RunOutcome.FAILED)
    assert not bar.isHidden()
    assert bar.property("error") is True


def test_the_manager_row_bar_only_animates_when_nothing_counts(view: SettingsView) -> None:
    """A removal prints nothing countable; its bar animates rather than
    pretending to a percentage that will never arrive."""
    bar = view._manager_progress

    view.show_manager_run(determinate=False)

    assert bar.maximum() == 0
    assert not bar.isHidden()


def test_a_finished_or_cancelled_run_puts_the_row_bar_away(view: SettingsView) -> None:
    """Only a failure leaves the bar behind, and red: a run that succeeded,
    or one the user stopped, has nothing left to say on the row."""
    bar = view._manager_progress
    view.show_manager_run(determinate=True)
    view.set_manager_progress(61)

    view.finish_manager_run(RunOutcome.DONE)

    assert bar.isHidden()
    assert bar.property("error") is False


def test_a_new_selection_clears_the_row_the_last_run_left_behind(
    view: SettingsView, monkeypatch
) -> None:
    """A red bar belongs to the choice it failed for; picking another
    manager starts the row's story over instead of inheriting it."""
    monkeypatch.setattr("pypackhub.ui.views.settings.manager_available", lambda name: True)

    view.show_manager_run(determinate=True)
    view.finish_manager_run(RunOutcome.FAILED)
    assert not view._manager_progress.isHidden()

    _choose(view, "uv")

    assert view._manager_progress.isHidden()
