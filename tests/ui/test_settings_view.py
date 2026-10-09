"""The settings page: population, change handling and re-translation.

The page is the only place the user can change a preference, so these tests cover
the three things that could quietly go wrong: showing stored settings (which must
not look like an edit), editing a setting (which must reach the shell and the store
once), and switching language (which must re-render the whole page, not just the
combo boxes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel

from pypackhub.core.settings import AppSettings, ColorTheme, Language
from pypackhub.core.validation import ValidationError
from pypackhub.ui.i18n.translations import EN_TABLE, FA_TABLE, TranslationKey
from pypackhub.ui.views.settings import SettingsView
from pypackhub.ui.views.window_shell import QSettingsStore, WindowShell

pytestmark = pytest.mark.gui


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
