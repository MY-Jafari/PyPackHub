"""The window shell's state: persistence rules and theme/language coordination.

The persistence layer is the one place where values that were written by an older
version, or edited by hand, meet the typed settings model. These tests feed it the
awkward shapes that actually occur - a bool written as ``"0"``, a width written as
a string, a list written as newline-separated text - and check it comes back safe.
"""

from __future__ import annotations

import dataclasses

import pytest

from pypackhub.core.settings import AppSettings, ColorTheme, Language
from pypackhub.ui.i18n.direction import Direction
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.theme import ThemeName
from pypackhub.ui.views.window_shell import SettingsPersistence, WindowShell


@pytest.fixture
def persistence() -> SettingsPersistence:
    return SettingsPersistence()


def test_round_trip_of_a_full_settings_object(persistence: SettingsPersistence) -> None:
    original = AppSettings(
        theme=ColorTheme.DARK,
        language=Language.FA,
        sidebar_width=300,
        package_list_page_size=120,
        pypi_index_url="https://pypi.org/simple",
        extra_index_urls=("https://example.test/simple",),
        show_sizes_by_default=False,
        confirm_before_install=False,
        auto_refresh_interval_seconds=45.0,
    )

    restored = persistence.read(persistence.write(original))
    # The credential store is not serialised (it holds no secrets of its own; the
    # secrets live in the OS keychain), so put the original instance back before
    # comparing.
    assert dataclasses.replace(restored, credential_store=original.credential_store) == original


def test_reading_an_empty_mapping_gives_safe_defaults(persistence: SettingsPersistence) -> None:
    settings = persistence.read({})
    assert settings.theme is ColorTheme.SYSTEM
    assert settings.language is Language.SYSTEM
    assert settings.sidebar_width == 220
    assert settings.confirm_before_install is True


def test_string_written_booleans_are_understood(persistence: SettingsPersistence) -> None:
    settings = persistence.read(
        {"show_sizes": "0", "show_outdated": "no", "confirm_install": "false"}
    )
    assert settings.show_sizes_by_default is False
    assert settings.show_outdated_by_default is False
    assert settings.confirm_before_install is False


def test_string_written_numbers_are_understood(persistence: SettingsPersistence) -> None:
    settings = persistence.read({"sidebar_width": "180", "auto_refresh": "12.5"})
    assert settings.sidebar_width == 180
    assert settings.auto_refresh_interval_seconds == 12.5


def test_junk_values_fall_back_instead_of_raising(persistence: SettingsPersistence) -> None:
    settings = persistence.read(
        {
            "theme": "chartreuse",
            "language": 5,
            "sidebar_width": "wide",
            "auto_refresh": object(),
            "extra_index_urls": 12,
        }
    )
    assert settings.theme is ColorTheme.SYSTEM
    assert settings.language is Language.SYSTEM
    assert settings.sidebar_width == 220
    assert settings.extra_index_urls == ()


def test_newline_written_lists_are_split(persistence: SettingsPersistence) -> None:
    settings = persistence.read(
        {"extra_index_urls": "https://a.test/simple\n\nhttps://b.test/simple\n"}
    )
    assert settings.extra_index_urls == (
        "https://a.test/simple",
        "https://b.test/simple",
    )


def test_written_settings_survive_a_validation_pass(persistence: SettingsPersistence) -> None:
    written = persistence.write(AppSettings(sidebar_width=10_000))
    assert persistence.read(written).sidebar_width == 600


def test_shell_starts_from_validated_defaults() -> None:
    shell = WindowShell()
    # The credential store is not part of the persisted model (it holds no secret
    # itself; the secrets live in the OS keychain), so compare the fields that are.
    default = AppSettings()
    assert dataclasses.replace(shell.settings, credential_store=default.credential_store) == default
    assert shell.direction_state.direction is Direction.LEFT_TO_RIGHT
    assert shell.font_family == "Inter"


def test_persian_language_switches_direction_and_font() -> None:
    shell = WindowShell()
    shell.set_language(Language.FA)
    assert shell.settings.language is Language.FA
    assert shell.is_rtl is True
    assert shell.direction_state.direction is Direction.RIGHT_TO_LEFT
    assert shell.font_family == "Vazirmatn"


def test_theme_setting_changes_the_active_tokens() -> None:
    shell = WindowShell()
    shell.set_theme(ColorTheme.DARK)
    assert shell.active_theme.variant.value == "dark"
    assert "#0F1218" in shell.active_theme.stylesheet()


def test_theme_toggle_moves_between_light_and_dark() -> None:
    shell = WindowShell()
    shell.set_theme(ColorTheme.LIGHT)
    shell.toggle_theme()
    assert shell.settings.theme is ColorTheme.DARK
    shell.toggle_theme()
    assert shell.settings.theme is ColorTheme.LIGHT


def test_language_toggle_moves_between_english_and_persian() -> None:
    shell = WindowShell()
    shell.toggle_language()
    assert shell.settings.language is Language.FA
    shell.toggle_language()
    assert shell.settings.language is Language.EN


def test_system_theme_resolves_to_a_concrete_variant() -> None:
    shell = WindowShell()
    shell.set_theme(ColorTheme.SYSTEM)
    assert shell.active_theme.name in {ThemeName.LIGHT, ThemeName.DARK}
    assert shell.active_theme.resolved_system_theme is shell.active_theme.name


def test_text_is_translated_in_the_current_language() -> None:
    shell = WindowShell()
    assert shell.text(TranslationKey.NAV_PACKAGES) == "Packages"
    shell.set_language(Language.FA)
    assert shell.text(TranslationKey.NAV_PACKAGES) == "\u0628\u0633\u062a\u0647\u200c\u0647\u0627"


def test_save_without_a_store_is_a_no_op() -> None:
    shell = WindowShell()
    shell.toggle_theme()
    shell.save()  # must not raise
    assert shell.load() == shell.settings
